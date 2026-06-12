"""Shared implementation for the five ``datatruck_*`` resource mixins.

Every Datatruck resource table follows the same ELT shape:

  * ``account_id`` + ``external_id`` identity with a UNIQUE pair —
    upserts are idempotent per (account, upstream id).
  * A handful of **promoted columns** we query/join on (unit numbers,
    VINs, statuses, …) — extracted by the sync layer, NOT here.
  * ``payload`` — the full raw JSON record from the Datatruck API.
    Keeps every field we didn't promote, so promoting one later is a
    migration + backfill from local data instead of a re-fetch
    through Datatruck's 20 req/min straw.
  * ``first_seen_at`` / ``synced_at`` stamps.

The resource files (``drivers.py``, ``trucks.py``, …) each declare a
dataclass + a ``_TableSpec`` and delegate to these helpers, so the
SQL lives in exactly one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class TableSpec:
    """Static description of one ``datatruck_*`` table.

    ``columns`` is the ordered tuple of promoted column names —
    everything between ``external_id`` and ``payload`` in the schema.
    The sync layer passes row dicts keyed by these names; missing
    keys never crash the writer.

    ``nullable`` names the numeric columns (year, odometer, rates,
    costs) whose schema allows NULL.  Every other promoted column is
    TEXT NOT NULL DEFAULT '' — a missing value is coerced to ''
    because an explicit NULL in an INSERT bypasses the column
    DEFAULT and trips the constraint.
    """

    table: str
    columns: tuple[str, ...]
    nullable: frozenset[str] = frozenset()

    def coerce(self, column: str, value: Any) -> Any:
        if value is None and column not in self.nullable:
            return ""
        return value


def _coerce_payload(value: Any) -> str:
    """Raw API record → TEXT column.  Accepts a dict (normal path) or
    a pre-serialised string (tests, re-imports)."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value or {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


async def upsert_rows(
    db: Any,
    spec: TableSpec,
    account_id: int,
    rows: Sequence[dict[str, Any]],
) -> int:
    """Idempotent bulk upsert.  Returns the number of rows written.

    Each row dict carries the promoted columns (missing → None),
    ``external_id`` (required — rows without one are skipped), and
    ``payload`` (dict or JSON string).  ``synced_at`` is stamped here;
    ``first_seen_at`` only on first insert (the conflict path leaves
    it untouched).
    """
    if not rows:
        return 0
    now = db._now()
    cols_csv = ", ".join(spec.columns)
    placeholders = ", ".join("?" for _ in spec.columns)
    update_csv = ", ".join(
        f"{c}=excluded.{c}" for c in spec.columns
    )
    sql = (
        f"INSERT INTO {spec.table} "
        f"(account_id, external_id, {cols_csv}, payload, first_seen_at, synced_at) "
        f"VALUES (?, ?, {placeholders}, ?, ?, ?) "
        f"ON CONFLICT(account_id, external_id) DO UPDATE SET "
        f"{update_csv}, payload=excluded.payload, synced_at=excluded.synced_at"
    )
    written = 0
    async with db.transaction():
        for row in rows:
            ext_id = str(row.get("external_id") or "").strip()
            if not ext_id:
                continue  # upstream record without an id — nothing to key on
            params = (
                account_id,
                ext_id,
                *(spec.coerce(c, row.get(c)) for c in spec.columns),
                _coerce_payload(row.get("payload")),
                now,
                now,
            )
            await db._db.execute(sql, params)
            written += 1
    return written


async def list_rows(
    db: Any,
    spec: TableSpec,
    account_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list:
    """Read rows for one account, most recently synced first.  Rows
    come back as raw records — the resource mixin maps them to its
    dataclass."""
    return await db.read_all(
        f"SELECT id, account_id, external_id, {', '.join(spec.columns)}, "
        f"payload, first_seen_at, synced_at "
        f"FROM {spec.table} WHERE account_id = ? "
        f"ORDER BY synced_at DESC, id DESC LIMIT ? OFFSET ?",
        (account_id, int(limit), int(offset)),
    )


async def resource_stats(
    db: Any,
    spec: TableSpec,
    account_id: int,
) -> dict[str, Any]:
    """``{count, last_synced_at}`` for the Integration card's sync
    badge — cheap single-row aggregate."""
    row = await db.read_one(
        f"SELECT COUNT(*), MAX(synced_at) FROM {spec.table} "
        f"WHERE account_id = ?",
        (account_id,),
    )
    return {
        "count": int(row[0] or 0) if row else 0,
        "last_synced_at": (row[1] if row else None) or None,
    }
