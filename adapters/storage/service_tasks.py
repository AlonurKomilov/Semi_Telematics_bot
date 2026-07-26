"""Service tasks — the shared vocabulary Maintenance and Work Orders
both speak.

Before this module the same idea lived as a bare TEXT slug in three
hand-maintained lists (frontend dropdown, maintenance AI tool, the
work-order link matcher) that had already drifted out of sync: the UI
offered "electrical" the backend would coerce to custom, while the AI
could mint "coolant"/"battery" tasks the dropdown couldn't render.
One table, one vocabulary, two consumers.

Identity model (advisor decision, 2026-07-26): standard tasks are
SEEDED PER ACCOUNT rather than referenced from a platform directory —
the parts/vendors directory earns its keep on DISCOVERY of an
open-ended vocabulary, which this closed ~20-item list doesn't need.
Cross-account comparability instead rides ``canonical_key``: every
account's "Engine Oil & Filter Replacement" carries the same key, so
fleet-wide benchmarking is a GROUP BY and the door to a real platform
library stays open (the keys already align).

  canonical_key != ''  ⇒ standard task: archive-only, name locked
  canonical_key == ''  ⇒ the account's own: renamable, deletable
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("bot.storage")

# Lifecycle vocabulary for a task DEFINITION (not a scheduled task).
TASK_ACTIVE = "active"
TASK_ARCHIVED = "archived"


def service_task_name_key(name: str) -> str:
    """Trim, collapse inner whitespace, casefold — the same
    normalization parts and vendors use, so duplicate names differing
    only by spacing/case collide instead of splitting reports."""
    return " ".join((name or "").split()).casefold()


# The seeded library.  Keys deliberately REUSE the historical slugs
# (oil, tires, brakes, …) so the backfill maps existing rows 1:1 with
# no guesswork; the extra entries are standard truck/trailer work the
# old hardcoded dropdown never offered.
STANDARD_SERVICE_TASKS: tuple[dict[str, str], ...] = (
    {"key": "inspection",      "name": "General Inspection"},
    {"key": "pm_service",      "name": "Preventive Maintenance Service"},
    {"key": "oil",             "name": "Engine Oil & Filter Replacement"},
    {"key": "tires",           "name": "Tire Service"},
    {"key": "alignment",       "name": "Wheel Alignment"},
    {"key": "brakes",          "name": "Brake Service"},
    {"key": "transmission",    "name": "Transmission Service"},
    {"key": "electrical",      "name": "Electrical Repair"},
    {"key": "battery",         "name": "Battery Service"},
    {"key": "coolant",         "name": "Cooling System Service"},
    {"key": "dot_inspection",  "name": "DOT Annual Inspection"},
    {"key": "dpf_regen",       "name": "DPF / Aftertreatment Service"},
    {"key": "def_refill",      "name": "DEF Refill"},
    {"key": "air_filter",      "name": "Air Filter Replacement"},
    {"key": "fuel_filter",     "name": "Fuel Filter Replacement"},
    {"key": "lube",            "name": "Chassis Lubrication"},
    {"key": "suspension",      "name": "Suspension Repair"},
    {"key": "air_system",      "name": "Air System & Air Lines"},
    {"key": "hvac",            "name": "HVAC / A-C Service"},
    {"key": "lighting",        "name": "Lighting Repair"},
    {"key": "trailer_service", "name": "Trailer Service"},
    # The legacy catch-all the old dropdown shipped with.  Kept as a
    # standard entry so historical rows tagged 'custom' keep a label.
    {"key": "custom",          "name": "Custom / Other"},
)

_STANDARD_BY_KEY = {t["key"]: t for t in STANDARD_SERVICE_TASKS}


class ServiceTasksMixin:
    """CRUD + seeding + the fail-open resolver for service tasks."""

    # ── Seeding ──────────────────────────────────────────────────────

    async def seed_service_tasks(self, account_id: int) -> int:
        """Insert any missing standard tasks for this account.

        Idempotent by ``(account_id, name_key)`` — safe to call on
        account creation AND from the migration's backfill.  Returns
        how many rows this call created.
        """
        now = self._now()
        created = 0
        for entry in STANDARD_SERVICE_TASKS:
            cur = await self._db.execute(
                "INSERT INTO service_tasks "
                "(account_id, name, name_key, canonical_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING "
                "RETURNING id",
                (account_id, entry["name"], service_task_name_key(entry["name"]),
                 entry["key"], now, now),
            )
            created += 1 if await cur.fetchone() else 0
        await self._db.commit()
        return created

    # ── Reads ────────────────────────────────────────────────────────

    async def list_service_tasks(
        self, account_id: int, *, include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """The account's tasks, standards first then custom, A-Z.

        ``include_archived`` is what report name-joins pass — an
        archived task must still resolve to its label or historical
        rows lose their names.
        """
        q = "SELECT * FROM service_tasks WHERE account_id = ?"
        params: list = [account_id]
        if not include_archived:
            q += " AND status = ?"
            params.append(TASK_ACTIVE)
        q += " ORDER BY CASE WHEN canonical_key = '' THEN 1 ELSE 0 END, name"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def get_service_task(
        self, task_id: int, account_id: int,
    ) -> Optional[dict[str, Any]]:
        cur = await self._db.execute(
            "SELECT * FROM service_tasks WHERE id = ? AND account_id = ?",
            (task_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def service_task_labels(self, account_id: int) -> dict[int, str]:
        """``{id: name}`` including ARCHIVED tasks — the join every
        report uses so a historical row never renders as a bare id."""
        cur = await self._db.execute(
            "SELECT id, name FROM service_tasks WHERE account_id = ?",
            (account_id,),
        )
        return {int(r["id"]): r["name"] for r in (dict(x) for x in await cur.fetchall())}

    # ── Writes ───────────────────────────────────────────────────────

    async def create_service_task(
        self, account_id: int, name: str, *,
        description: str = "",
        expected_labor_hours: float = 0.0,
        parent_id: Optional[int] = None,
        canonical_key: str = "",
        created_by: int = 0,
    ) -> Optional[dict[str, Any]]:
        """Create a task; ``None`` when the name collides (the caller
        surfaces "that task already exists" — Fleetio's unique-name
        rule, which is what keeps reports from splitting).

        ``parent_id`` must name a TOP-LEVEL task: one level of nesting
        only, enforced here because no CHECK constraint can express
        "my parent must not itself have a parent".
        """
        name = (name or "").strip()
        if not name:
            return None
        if parent_id is not None:
            parent = await self.get_service_task(int(parent_id), account_id)
            if not parent or parent.get("parent_id"):
                return None          # missing, or already a subtask
        now = self._now()
        # Explicit RETURNING id: with an ON CONFLICT clause the adapter
        # skips its own RETURNING augmentation, so ``lastrowid`` would
        # be 0.  Zero rows back == the name was taken.
        cur = await self._db.execute(
            "INSERT INTO service_tasks "
            "(account_id, name, name_key, canonical_key, description, "
            " expected_labor_hours, parent_id, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id, name_key) DO NOTHING "
            "RETURNING id",
            (account_id, name, service_task_name_key(name), canonical_key,
             description, float(expected_labor_hours or 0), parent_id,
             created_by, now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        if not row:
            return None
        return await self.get_service_task(int(dict(row)["id"]), account_id)

    async def update_service_task(
        self, task_id: int, account_id: int, **fields: Any,
    ) -> bool:
        """Update a task.  A STANDARD task's name is locked (renaming
        it would break the cross-account ``canonical_key`` contract);
        its description/labor estimate stay editable."""
        task = await self.get_service_task(task_id, account_id)
        if not task:
            return False
        allowed = {"description", "expected_labor_hours", "status", "parent_id"}
        if not task.get("canonical_key"):
            allowed.add("name")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "status" in updates and updates["status"] not in (TASK_ACTIVE, TASK_ARCHIVED):
            return False
        if "parent_id" in updates:
            pid = updates["parent_id"]
            if pid:
                parent = await self.get_service_task(int(pid), account_id)
                if not parent or parent.get("parent_id") or int(pid) == task_id:
                    return False
        if "name" in updates:
            new_name = str(updates["name"]).strip()
            if not new_name:
                return False
            updates["name"] = new_name
            updates["name_key"] = service_task_name_key(new_name)
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = [*updates.values(), self._now(), task_id, account_id]
        try:
            cur = await self._db.execute(
                f"UPDATE service_tasks SET {sets}, updated_at = ? "
                f"WHERE id = ? AND account_id = ?", params,
            )
        except Exception:
            return False             # name_key collision → caller reports it
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def service_task_usage(self, task_id: int, account_id: int) -> int:
        """How many live rows reference this task — the delete guard
        (archive keeps history; delete is only for unreferenced customs)."""
        total = 0
        for table, col in (
            ("maintenance_tasks", "account_id"),
            ("work_order_parts", None),
            ("work_order_labor", "account_id"),
        ):
            q = f"SELECT COUNT(*) AS n FROM {table} WHERE service_task_id = ?"
            params: list = [task_id]
            if col:
                q += f" AND {col} = ?"
                params.append(account_id)
            try:
                cur = await self._db.execute(q, params)
                total += int(dict(await cur.fetchone())["n"])
            except Exception:        # pragma: no cover — pre-migration
                continue
        return total

    async def delete_service_task(self, task_id: int, account_id: int) -> bool:
        """Delete a CUSTOM, UNREFERENCED task.  Standard tasks are
        archive-only, and anything with history stays so its rows keep
        their label."""
        task = await self.get_service_task(task_id, account_id)
        if not task or task.get("canonical_key"):
            return False
        if await self.service_task_usage(task_id, account_id):
            return False
        cur = await self._db.execute(
            "DELETE FROM service_tasks WHERE id = ? AND account_id = ? "
            "AND canonical_key = ''",
            (task_id, account_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # ── The resolver (fail-open) ─────────────────────────────────────

    async def resolve_service_task(
        self, account_id: int, value: str, *, created_by: int = 0,
    ) -> Optional[dict[str, Any]]:
        """Slug/label → the task row, creating one if we've never seen it.

        THE contract for every writer (bot, AI tool, fault auto-create,
        both forms): a task vocabulary must never reject or silently
        drop a write.  An unknown value becomes an ARCHIVED custom row
        — the data keeps its meaning and the operator can promote or
        merge it later, but it doesn't clutter live dropdowns.

        Accepts a canonical key ('oil'), a legacy custom slug
        ('custom_brake_job') or a human label ('Brake Job').
        """
        raw = (value or "").strip()
        if not raw:
            return None

        # 1) canonical key (the historical slug)
        cur = await self._db.execute(
            "SELECT * FROM service_tasks "
            "WHERE account_id = ? AND canonical_key = ?",
            (account_id, raw.lower()),
        )
        row = await cur.fetchone()
        if row:
            return dict(row)

        # 2) exact name (normalized)
        cur = await self._db.execute(
            "SELECT * FROM service_tasks WHERE account_id = ? AND name_key = ?",
            (account_id, service_task_name_key(raw)),
        )
        row = await cur.fetchone()
        if row:
            return dict(row)

        # 3) a legacy 'custom_<slug>' value → try its de-namespaced label
        label = raw
        if raw.lower().startswith("custom_"):
            label = raw[len("custom_"):].replace("_", " ").strip() or raw
            cur = await self._db.execute(
                "SELECT * FROM service_tasks WHERE account_id = ? AND name_key = ?",
                (account_id, service_task_name_key(label)),
            )
            row = await cur.fetchone()
            if row:
                return dict(row)

        # 4) never seen → archived custom row, so the write survives
        created = await self.create_service_task(
            account_id, label.title() if label.islower() else label,
            created_by=created_by,
        )
        if created:
            await self.update_service_task(
                created["id"], account_id, status=TASK_ARCHIVED,
            )
            logger.info(
                "service_tasks: auto-created archived task %r for account %s",
                label, account_id,
            )
            return await self.get_service_task(created["id"], account_id)
        # Lost a race — re-read.
        cur = await self._db.execute(
            "SELECT * FROM service_tasks WHERE account_id = ? AND name_key = ?",
            (account_id, service_task_name_key(label)),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def resolve_service_task_id(
        self, account_id: int, value: str, *, created_by: int = 0,
    ) -> Optional[int]:
        """``resolve_service_task`` → just the id (what writers store)."""
        task = await self.resolve_service_task(
            account_id, value, created_by=created_by,
        )
        return int(task["id"]) if task else None
