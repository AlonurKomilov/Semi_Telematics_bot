"""The operator-curated standard service-task library (platform).

The standard task list used to be a Python tuple, so adding one
fleet-wide meant a code change plus a migration.  This makes it data
the system owner edits on system.4truck.us — the same control they
already have over the parts and vendor directories.

Why this is simpler than ``part_directory``: parts and vendors needed
adopt fan-out, aliases and suppression flags because their vocabulary
is open-ended and accounts DISCOVER entries by messy name matching.
Service tasks don't — every account's standard task already carries
``canonical_key``, which IS the cross-account identity, so the library
and the tenant rows line up by construction.  A fan-out here is a
straight upsert on that key, not a matching problem.

Contracts worth knowing:

  * **Adding** a library entry fans it out to every account, otherwise
    a new standard task would only ever reach accounts created later.
  * **Renaming** fans out too — the whole point of a shared key is that
    "Brake Service" means the same thing everywhere; letting names
    drift per account would undo it.  An account that ARCHIVED its copy
    keeps it archived: the rename updates the label, never the state.
  * **Archiving** a library entry stops it being seeded into NEW
    accounts and does NOT touch existing ones — they may have years of
    history under it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from adapters.storage.service_tasks import (
    SYSTEM_KEYS, service_task_name_key,
)

logger = logging.getLogger("bot.storage")

LIB_ACTIVE = "active"
LIB_ARCHIVED = "archived"


def canonical_key_from(name: str) -> str:
    """Derive a stable key from a name — lowercase, underscores.

    Keys are the cross-account identity, so they must be stable and
    boring: no spaces, no punctuation, no case.
    """
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug[:60]


class ServiceTaskLibraryMixin:
    """Operator CRUD over the standard library + fan-out to accounts."""

    async def list_service_task_library(
        self, *, include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM service_task_library"
        params: list = []
        if not include_archived:
            q += " WHERE status = ?"
            params.append(LIB_ACTIVE)
        q += " ORDER BY name"
        cur = await self._db.execute(q, params)
        rows = [dict(r) for r in await cur.fetchall()]
        # How many accounts actually hold each one — the operator's
        # sanity check before renaming or archiving anything.
        try:
            cur = await self._db.execute(
                "SELECT canonical_key, COUNT(*) AS n FROM service_tasks "
                "WHERE canonical_key <> '' GROUP BY canonical_key",
            )
            counts = {r["canonical_key"]: int(r["n"])
                      for r in (dict(x) for x in await cur.fetchall())}
        except Exception:      # pragma: no cover — pre-migration
            counts = {}
        for r in rows:
            r["accounts"] = counts.get(r["canonical_key"], 0)
        return rows

    async def get_service_task_library_entry(
        self, entry_id: int,
    ) -> Optional[dict[str, Any]]:
        cur = await self._db.execute(
            "SELECT * FROM service_task_library WHERE id = ?", (entry_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_service_task_library_entry(
        self, name: str, *,
        description: str = "",
        expected_labor_hours: float = 0.0,
        vehicle_type: str = "",
        system_key: str = "",
    ) -> Optional[dict[str, Any]]:
        """Add a standard task and hand it to every account.

        ``None`` when the name is blank or its key already exists —
        two library entries sharing a key would make the identity
        ambiguous, which is the one thing this table exists to prevent.
        """
        name = (name or "").strip()
        key = canonical_key_from(name)
        if not name or not key:
            return None
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO service_task_library "
            "(canonical_key, name, description, expected_labor_hours, "
            " vehicle_type, system_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (canonical_key) DO NOTHING "
            "RETURNING id",
            (key, name, description, float(expected_labor_hours or 0),
             vehicle_type if vehicle_type in ("truck", "trailer") else "",
             system_key if system_key in SYSTEM_KEYS else "", now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        if not row:
            return None
        entry = await self.get_service_task_library_entry(int(dict(row)["id"]))
        if entry:
            await self.fan_out_service_task_library_entry(entry)
        return entry

    async def update_service_task_library_entry(
        self, entry_id: int, **fields: Any,
    ) -> bool:
        """Edit an entry.  The ``canonical_key`` is immutable — it's the
        identity every account's copy is matched on, so changing it
        would orphan all of them.  A name change DOES fan out."""
        entry = await self.get_service_task_library_entry(entry_id)
        if not entry:
            return False
        allowed = {"name", "description", "expected_labor_hours",
                   "vehicle_type", "system_key", "status"}
        updates = {k: v for k, v in fields.items()
                   if k in allowed and v is not None}
        if not updates:
            return False
        if "status" in updates and updates["status"] not in (LIB_ACTIVE, LIB_ARCHIVED):
            return False
        if "vehicle_type" in updates:
            vt = str(updates["vehicle_type"]).strip().lower()
            if vt not in ("", "truck", "trailer"):
                return False
            updates["vehicle_type"] = vt
        if "system_key" in updates:
            sk = str(updates["system_key"]).strip().lower()
            if sk and sk not in SYSTEM_KEYS:
                return False
            updates["system_key"] = sk
        if "name" in updates:
            nm = str(updates["name"]).strip()
            if not nm:
                return False
            updates["name"] = nm
        sets = ", ".join(f"{k} = ?" for k in updates)
        await self._db.execute(
            f"UPDATE service_task_library SET {sets}, updated_at = ? WHERE id = ?",
            [*updates.values(), self._now(), entry_id],
        )
        await self._db.commit()
        fresh = await self.get_service_task_library_entry(entry_id)
        # Push label/estimate changes down; archiving deliberately does
        # NOT (existing accounts keep what they have and its history).
        if fresh and fresh["status"] == LIB_ACTIVE and (
            "name" in updates or "description" in updates
            or "expected_labor_hours" in updates or "vehicle_type" in updates
            or "system_key" in updates
        ):
            await self.fan_out_service_task_library_entry(fresh)
        return True

    async def service_task_candidates(
        self, *, min_accounts: int = 2, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Custom task names used by ≥ N accounts that match no library
        entry — the operator's promote-to-standard signal, same shape
        as the parts-directory candidates queue.  OPERATOR-ONLY data
        (names + counts cross accounts); the account-facing page never
        sees usage numbers."""
        cur = await self._db.execute(
            "SELECT st.name_key, MIN(st.name) AS sample_name, "
            "       COUNT(DISTINCT st.account_id) AS account_count "
            "FROM service_tasks st "
            "WHERE st.canonical_key = '' "
            "GROUP BY st.name_key "
            "HAVING COUNT(DISTINCT st.account_id) >= ? "
            f"ORDER BY account_count DESC, st.name_key LIMIT {int(limit)}",
            (min_accounts,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        # Names already covered by the library (by normalized name)
        # aren't candidates — they'd adopt on the next fan-out anyway.
        lib = await self.list_service_task_library()
        covered = {service_task_name_key(e["name"]) for e in lib}
        return [r for r in rows if r["name_key"] not in covered]

    async def fan_out_service_task_library_entry(
        self, entry: dict[str, Any],
    ) -> int:
        """Ensure every account holds this standard task, matched on
        ``canonical_key``.

        Accounts that already have it get the label and estimate
        refreshed; their ARCHIVED state is preserved, because hiding a
        task they don't do is their decision, not ours.  Returns the
        number of accounts that gained a new row.
        """
        key = entry["canonical_key"]
        name = entry["name"]
        now = self._now()

        # ADOPT before insert: an account that already created this
        # task as its own custom (same normalized name) gets that row
        # PROMOTED in place — canonical_key stamped onto it — instead
        # of being skipped by the conflict clause.  Their history stays
        # on the same row id, so every maintenance/work-order reference
        # keeps pointing at it; only the identity upgrades.  This is
        # the "my task merges with the public one" path.
        await self._db.execute(
            "UPDATE service_tasks SET canonical_key = ? "
            "WHERE name_key = ? AND canonical_key = ''",
            (key, service_task_name_key(name)),
        )

        # Identity is pushed; local tuning is only FILLED.
        #
        # Name and system_key overwrite unconditionally — they are the
        # shared contract, and a fleet holding a stale name or a
        # different reporting axis is exactly the drift the canonical
        # key exists to prevent.
        #
        # description / expected_labor_hours / vehicle_type land only
        # while the account's value is still blank.  Before this, an
        # operator fixing a typo in one entry silently reset every
        # fleet's tuned labor estimate — the same "never silently
        # overwrite" rule the invoice prefill follows.
        await self._db.execute(
            "UPDATE service_tasks SET name = ?, name_key = ?, system_key = ?, "
            "       description = CASE WHEN COALESCE(description, '') = '' "
            "                          THEN ? ELSE description END, "
            "       expected_labor_hours = CASE "
            "                          WHEN COALESCE(expected_labor_hours, 0) = 0 "
            "                          THEN ? ELSE expected_labor_hours END, "
            "       vehicle_type = CASE WHEN COALESCE(vehicle_type, '') = '' "
            "                          THEN ? ELSE vehicle_type END, "
            "       updated_at = ? "
            "WHERE canonical_key = ?",
            (name, service_task_name_key(name), entry.get("system_key") or "",
             entry.get("description") or "",
             float(entry.get("expected_labor_hours") or 0),
             entry.get("vehicle_type") or "", now, key),
        )

        cur = await self._db.execute(
            "SELECT id FROM accounts WHERE id NOT IN "
            "  (SELECT account_id FROM service_tasks WHERE canonical_key = ?)",
            (key,),
        )
        created = 0
        for row in [dict(r) for r in await cur.fetchall()]:
            c = await self._db.execute(
                "INSERT INTO service_tasks "
                "(account_id, name, name_key, canonical_key, description, "
                " expected_labor_hours, vehicle_type, system_key, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING "
                "RETURNING id",
                (int(row["id"]), name, service_task_name_key(name), key,
                 entry.get("description") or "",
                 float(entry.get("expected_labor_hours") or 0),
                 entry.get("vehicle_type") or "",
                 entry.get("system_key") or "", now, now),
            )
            if await c.fetchone():
                created += 1
        await self._db.commit()
        logger.info("service_task_library: %r fanned out to %d new accounts",
                    key, created)
        return created
