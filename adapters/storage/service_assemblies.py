"""Assemblies — level 2 of System → Assembly → Part.

The middle rung: a part belongs to an assembly, the assembly belongs
to exactly one system, so "Cooling cost us $12k" can open into
"…of which Radiator $7k".  Lives on the PART side (``parts_catalog.
assembly_key``) because an assembly describes the thing touched — a
radiator hose is part of the radiator no matter which job used it.

Owner decisions (2026-07-27):
  * OPERATOR-EDITABLE on system.4truck.us (same principle as the
    service-task library: shared-across-accounts vocabulary belongs in
    the operator console, especially while new).  Seeded from the
    tuple below as bootstrap; the platform table is the source of
    truth afterwards — seeding never re-asserts labels, so operator
    edits win.
  * This is OUR taxonomy, not licensed VMRS; a ``vmrs_code`` can sit
    beside it later.

Advisor rules (2026-07-27, binding):
  * ``key`` AND ``system_key`` are IMMUTABLE — re-parenting an
    assembly would rewrite historical rollups retroactively.  Fix a
    wrong parent by archive + recreate.
  * Label resolution FAILS OPEN: an unknown/archived key renders its
    raw key rather than erroring, and archived keys stay valid on
    existing parts (only NEW assignments require an active key).
  * ``assembly_key`` on parts is optional — consumables (grease,
    hardware) have no assembly and render as "Unassigned"; inventing
    a junk assembly for them would be worse.
  * THE DELEGATION RULE: labor always rolls to the task's system.
    Parts on a COMPONENT-system task roll to the task's system too
    (the owner's "task wins").  But parts on an ACTIVITY-system task
    (pm / inspection / other — and untagged lines) delegate to their
    assembly's system, else a PM-heavy fleet's drill-down would be
    empty and an oil filter bought in a PM would count as "PM" spend
    forever.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from adapters.storage.service_taxonomy import SYSTEM_KEYS

logger = logging.getLogger("bot.storage")

ASM_ACTIVE = "active"
ASM_ARCHIVED = "archived"

# Task systems that DELEGATE parts spend to the part's assembly —
# they describe activity, not components (plus '' = untagged).
# The taxonomy itself lives in ``service_taxonomy`` — this module keeps
# only the DB access.  Re-exported at the old names so existing import
# sites are untouched.
from adapters.storage.service_taxonomy import (   # noqa: F401
    DELEGATING_SYSTEMS, SERVICE_ASSEMBLIES,
    normalize_assembly_key, suggest_assembly_for, system_rollup_case,
)

class ServiceAssembliesMixin:
    """Operator CRUD over the assembly library + tenant reads."""

    async def list_service_assemblies(
        self, *, include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM service_assembly_library"
        params: list = []
        if not include_archived:
            q += " WHERE status = ?"
            params.append(ASM_ACTIVE)
        q += " ORDER BY system_key, label"
        cur = await self._db.execute(q, params)
        rows = [dict(r) for r in await cur.fetchall()]
        # How many parts hold each key — the operator's sanity check.
        try:
            cur = await self._db.execute(
                "SELECT assembly_key, COUNT(*) AS n FROM parts_catalog "
                "WHERE assembly_key <> '' GROUP BY assembly_key",
            )
            counts = {r["assembly_key"]: int(r["n"])
                      for r in (dict(x) for x in await cur.fetchall())}
        except Exception:      # pragma: no cover — pre-migration
            counts = {}
        for r in rows:
            r["parts"] = counts.get(r["key"], 0)
        return rows

    async def assembly_labels(self) -> dict[str, dict[str, str]]:
        """key → {label, system_key}, INCLUDING archived (fail-open:
        a historical part must keep rendering its label)."""
        cur = await self._db.execute(
            "SELECT key, label, system_key FROM service_assembly_library",
        )
        return {r["key"]: {"label": r["label"], "system_key": r["system_key"]}
                for r in (dict(x) for x in await cur.fetchall())}

    async def create_service_assembly(
        self, label: str, system_key: str,
    ) -> Optional[dict[str, Any]]:
        """Operator add.  Key derives from the label once and never
        changes; the system parent is immutable after this moment
        (advisor rule — re-parenting rewrites history)."""
        label = (label or "").strip()
        key = normalize_assembly_key(label)
        if not label or not key or system_key not in SYSTEM_KEYS:
            return None
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO service_assembly_library "
            "(key, label, system_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (key) DO NOTHING RETURNING id",
            (key, label, system_key, now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        if not row:
            return None
        cur = await self._db.execute(
            "SELECT * FROM service_assembly_library WHERE id = ?",
            (int(dict(row)["id"]),),
        )
        got = await cur.fetchone()
        return dict(got) if got else None

    async def update_service_assembly(
        self, assembly_id: int, **fields: Any,
    ) -> bool:
        """Label and status only — key and system_key are immutable."""
        allowed = {"label", "status"}
        updates = {k: v for k, v in fields.items()
                   if k in allowed and v is not None}
        if not updates:
            return False
        if "status" in updates and updates["status"] not in (
                ASM_ACTIVE, ASM_ARCHIVED):
            return False
        if "label" in updates:
            lb = str(updates["label"]).strip()
            if not lb:
                return False
            updates["label"] = lb
        sets = ", ".join(f"{k} = ?" for k in updates)
        cur = await self._db.execute(
            f"UPDATE service_assembly_library SET {sets}, updated_at = ? "
            f"WHERE id = ?",
            [*updates.values(), self._now(), assembly_id],
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def assembly_key_valid_for_assignment(self, key: str) -> bool:
        """NEW assignments need an ACTIVE key; '' (clearing) is always
        fine.  Archived keys stay valid on rows that already hold them
        — that check is the caller's, this answers 'may I assign it'."""
        if not key:
            return True
        cur = await self._db.execute(
            "SELECT 1 FROM service_assembly_library "
            "WHERE key = ? AND status = ?",
            (key, ASM_ACTIVE),
        )
        return (await cur.fetchone()) is not None
