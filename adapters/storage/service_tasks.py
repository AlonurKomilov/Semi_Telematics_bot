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


# ── Systems: the reporting axis above a task ────────────────────────
#
# "Brakes cost us $12k this quarter" is the question a fleet actually
# asks, and a flat list of ~40 task names can't answer it.  A system is
# the coarse bucket every task belongs to.
#
# This is OUR taxonomy, not VMRS.  VMRS is the industry standard for
# the same idea, but its code set is licensed from TMC/ATA per-seat on
# revenue (verified 2026-07-27) and its value — OEM warranty claims,
# cross-industry benchmarking — needs interoperability we can't use
# yet.  The INTERNAL value (spend per system, failure patterns, a
# picker that stays usable) needs no licence at all, which is what this
# delivers.  A ``vmrs_code`` column can sit beside ``system_key`` later
# without disturbing any of it.
#
# Deliberately a CONSTANT, not an operator-curated table: this is the
# reporting axis, so consistency matters more than flexibility, and a
# stray extra system would fragment exactly the report it exists to
# produce.  It is served over the API (``GET /service-tasks/systems``)
# so the frontend never keeps a second copy — that second copy is
# precisely how the old task vocabulary drifted.
SERVICE_TASK_SYSTEMS: tuple[dict[str, str], ...] = (
    {"key": "pm",           "label": "Preventive Maintenance"},
    {"key": "inspection",   "label": "Inspection & Compliance"},
    {"key": "engine",       "label": "Engine"},
    {"key": "cooling",      "label": "Cooling System"},
    {"key": "fuel",         "label": "Fuel System"},
    {"key": "exhaust",      "label": "Exhaust & Aftertreatment"},
    {"key": "drivetrain",   "label": "Drivetrain & Transmission"},
    {"key": "brakes",       "label": "Brakes"},
    {"key": "air_system",   "label": "Air System"},
    {"key": "suspension",   "label": "Suspension"},
    {"key": "steering",     "label": "Steering & Alignment"},
    {"key": "tires_wheels", "label": "Tires & Wheels"},
    {"key": "electrical",   "label": "Electrical"},
    {"key": "lighting",     "label": "Lighting"},
    {"key": "hvac",         "label": "HVAC"},
    {"key": "body_cab",     "label": "Body & Cab"},
    {"key": "trailer",      "label": "Trailer"},
    {"key": "other",        "label": "Other"},
)

SYSTEM_KEYS = frozenset(s["key"] for s in SERVICE_TASK_SYSTEMS)
SYSTEM_LABELS = {s["key"]: s["label"] for s in SERVICE_TASK_SYSTEMS}


def normalize_system_key(value: str) -> str:
    """A recognised system key, else '' (uncategorized).  Never raises —
    an unknown value must not block a task write."""
    v = (value or "").strip().lower()
    return v if v in SYSTEM_KEYS else ""


# ── Suggesting a system from a task's name ─────────────────────────
#
# The fill path for tasks a human typed: suggest-confirm, never silent
# (the owner's standing rule).  Same discipline as the work-order link
# matcher: word-boundary matching and SPECIFIC words only — one hit is
# enough to suggest, so a generic word here would mislabel half the
# list.  A task the map doesn't recognise simply gets no suggestion.
_SYSTEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pm":           ("pm service", "preventive", "lube", "grease",
                     "lubrication"),
    "inspection":   ("inspection", "inspect", "dot", "fmcsa", "annual"),
    "engine":       ("engine", "oil change", "oil filter", "air filter",
                     "turbo", "turbocharger", "injector", "cylinder",
                     "valve cover", "crankcase", "egr"),
    "cooling":      ("coolant", "radiator", "water pump", "thermostat",
                     "antifreeze", "fan clutch", "overheat"),
    "fuel":         ("fuel filter", "fuel pump", "fuel line", "fuel tank",
                     "water separator", "diesel"),
    "exhaust":      ("dpf", "def", "regen", "aftertreatment", "scr",
                     "urea", "adblue", "exhaust", "muffler", "doc",
                     "nox sensor"),
    "drivetrain":   ("transmission", "clutch", "driveline", "driveshaft",
                     "differential", "gearbox", "u joint", "pto"),
    "brakes":       ("brake", "brakes", "rotor", "caliper", "drum",
                     "pad", "pads", "shoe", "shoes", "slack adjuster",
                     "abs"),
    "air_system":   ("air line", "air lines", "air dryer", "air leak",
                     "glad hand", "gladhand", "air compressor",
                     "air tank", "air bag", "airbag"),
    "suspension":   ("suspension", "spring", "shock", "bushing",
                     "torque rod", "leaf spring"),
    "steering":     ("steering", "alignment", "align", "tie rod",
                     "drag link", "kingpin", "king pin", "toe", "camber"),
    "tires_wheels": ("tire", "tires", "tyre", "wheel", "rim", "hub",
                     "bearing", "wheel seal", "rotation", "balance",
                     "retread", "flat"),
    "electrical":   ("electrical", "wiring", "harness", "battery",
                     "batteries", "alternator", "starter", "fuse",
                     "solenoid", "sensor"),
    "lighting":     ("light", "lights", "headlight", "taillight",
                     "marker", "bulb", "led"),
    "hvac":         ("hvac", "a/c", "ac ", "air conditioning", "heater",
                     "blower", "condenser", "evaporator", "apu",
                     "bunk heater"),
    "body_cab":     ("door", "mirror", "windshield", "glass", "cab",
                     "hood", "fender", "bumper", "seat", "paint",
                     "body"),
    "trailer":      ("trailer", "landing gear", "fifth wheel", "reefer",
                     "liftgate", "lift gate", "mud flap", "tarp",
                     "roll door", "swing door"),
}

import re as _re


def suggest_system_for(name: str) -> str:
    """Best-guess system for a task NAME, or '' when nothing specific
    matches.  A suggestion, never an assignment — the caller shows it
    and a human confirms.  Word-boundary matching so 'def' can't hit
    'defrost' (the same guard the link matcher uses); first match in
    declaration order wins, and the more specific multiword phrases
    are listed before the words they contain."""
    hay = " ".join((name or "").lower().split())
    if not hay:
        return ""
    # Longest matched keyword wins, not declaration order — "Kingpin
    # grease" must go to Steering (kingpin) even though PM's "grease"
    # also hits.  Specificity is length, near enough.
    best_system, best_len = "", 0
    for system, words in _SYSTEM_KEYWORDS.items():
        for w in words:
            if len(w) > best_len and _re.search(
                    rf"\b{_re.escape(w)}\b", hay):
                best_system, best_len = system, len(w)
    return best_system


# Filler that carries no meaning when comparing a task name against a
# system label — "Brakes Repair" IS the Brakes system plus noise.
_BUCKET_FILLER = frozenset({
    "repair", "repairs", "service", "services", "general", "other",
    "system", "systems", "work", "job", "jobs", "fix", "fixes",
    "and", "a", "c", "of", "the",
})


def system_bucket_collision(name: str) -> str:
    """The system key a task NAME collapses onto, or ''.

    Guard for the degenerate case the task list is designed to avoid:
    a user creating "Brakes Repair" or "Electrical" as a custom task —
    that's not a job, it's the system's general bucket again, and a
    second bucket splits the very report the system layer exists for.
    The seeded catch-alls are the sanctioned buckets; this stops the
    9th, 10th, 11th being born.
    """
    def toks(text: str) -> frozenset:
        return frozenset(
            t for t in _re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in _BUCKET_FILLER
        )
    name_toks = toks(name)
    if not name_toks:
        return ""
    for sy in SERVICE_TASK_SYSTEMS:
        if name_toks == toks(sy["label"]) or name_toks == toks(
                sy["key"].replace("_", " ")):
            return sy["key"]
    return ""


# Which system each seeded standard task belongs to.  Kept beside the
# task list so the two can't drift apart.
_STANDARD_SYSTEMS: dict[str, str] = {
    "inspection": "inspection", "dot_inspection": "inspection",
    "pm_service": "pm",         "lube": "pm",
    "oil": "engine",            "air_filter": "engine",
    "coolant": "cooling",       "fuel_filter": "fuel",
    "dpf_regen": "exhaust",     "def_refill": "exhaust",
    "transmission": "drivetrain",
    "brakes": "brakes",         "air_system": "air_system",
    "suspension": "suspension", "alignment": "steering",
    "tires": "tires_wheels",
    "electrical": "electrical", "battery": "electrical",
    "lighting": "lighting",     "hvac": "hvac",
    "trailer_service": "trailer",
    "body_cab_repair": "body_cab",
    "custom": "other",
}


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
    # Owner decision 2026-07-27: Body & Cab was the one system with no
    # task pointing at it — a door/mirror/cab invoice had nowhere to
    # land except Custom/Other.
    {"key": "body_cab_repair", "name": "Body & Cab Repair"},
    # The legacy catch-all the old dropdown shipped with.  Kept as a
    # standard entry so historical rows tagged 'custom' keep a label.
    {"key": "custom",          "name": "Custom / Other"},
)

_STANDARD_BY_KEY = {t["key"]: t for t in STANDARD_SERVICE_TASKS}


class ServiceTasksMixin:
    """CRUD + seeding + the fail-open resolver for service tasks."""

    # ── Seeding ──────────────────────────────────────────────────────

    async def _standard_seed_rows(self) -> list[dict[str, Any]]:
        """The standard list to seed from.

        The operator-curated ``service_task_library`` is the source of
        truth; the code tuple is the bootstrap for a database whose
        library hasn't been created/seeded yet (fresh installs, tests
        that skip platform migrations).  Archived library entries are
        skipped — that's what archiving one means: stop handing it to
        new accounts.
        """
        try:
            cur = await self._db.execute(
                "SELECT canonical_key, name, description, "
                "       expected_labor_hours, vehicle_type "
                "FROM service_task_library WHERE status = 'active' "
                "ORDER BY name",
            )
            rows = [dict(r) for r in await cur.fetchall()]
        except Exception:
            rows = []
        if rows:
            return [{"key": r["canonical_key"], "name": r["name"],
                     "description": r.get("description") or "",
                     "hours": float(r.get("expected_labor_hours") or 0),
                     "vehicle_type": r.get("vehicle_type") or "",
                     "system": r.get("system_key")
                               or _STANDARD_SYSTEMS.get(r["canonical_key"], "")}
                    for r in rows]
        return [{"key": e["key"], "name": e["name"], "description": "",
                 "hours": 0.0, "vehicle_type": "",
                 "system": _STANDARD_SYSTEMS.get(e["key"], "")}
                for e in STANDARD_SERVICE_TASKS]

    async def seed_service_tasks(self, account_id: int) -> int:
        """Insert any missing standard tasks for this account.

        Idempotent by ``(account_id, name_key)`` — safe to call on
        account creation AND from the migration's backfill.  Returns
        how many rows this call created.
        """
        now = self._now()
        created = 0
        for entry in await self._standard_seed_rows():
            cur = await self._db.execute(
                "INSERT INTO service_tasks "
                "(account_id, name, name_key, canonical_key, description, "
                " expected_labor_hours, vehicle_type, system_key, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING "
                "RETURNING id",
                (account_id, entry["name"], service_task_name_key(entry["name"]),
                 entry["key"], entry.get("description", ""),
                 entry.get("hours", 0.0), entry.get("vehicle_type", ""),
                 entry.get("system", ""), now, now),
            )
            created += 1 if await cur.fetchone() else 0
        await self._db.commit()
        return created

    # ── Reads ────────────────────────────────────────────────────────

    async def list_service_tasks(
        self, account_id: int, *, include_archived: bool = False,
        vehicle_type: str = "",
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
        # Narrowing is additive, never exclusive: a task tagged for
        # "any vehicle" ('') belongs on every list.
        if vehicle_type in ("truck", "trailer"):
            q += " AND vehicle_type IN ('', ?)"
            params.append(vehicle_type)
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

    async def find_service_task_by_name(
        self, account_id: int, name: str,
    ) -> Optional[dict[str, Any]]:
        """Read-only name lookup — what a rename checks against before
        it collides.  Never creates (that's the writers' resolver)."""
        key = service_task_name_key(name)
        if not key:
            return None
        cur = await self._db.execute(
            "SELECT * FROM service_tasks WHERE account_id = ? AND name_key = ?",
            (account_id, key),
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
        vehicle_type: str = "",
        system_key: str = "",
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
            " expected_labor_hours, parent_id, vehicle_type, system_key, "
            " created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id, name_key) DO NOTHING "
            "RETURNING id",
            (account_id, name, service_task_name_key(name), canonical_key,
             description, float(expected_labor_hours or 0), parent_id,
             vehicle_type if vehicle_type in ("truck", "trailer") else "",
             normalize_system_key(system_key), created_by, now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        if not row:
            return None
        return await self.get_service_task(int(dict(row)["id"]), account_id)

    async def update_service_task(
        self, task_id: int, account_id: int, **fields: Any,
    ) -> bool:
        """Update a task.

        On a STANDARD task the split is: identity is the operator's,
        local tuning is the account's.

        Locked here and pushed by fan-out — NAME, because it is the
        task's identity, and SYSTEM_KEY, because it is the axis every
        fleet's spend rolls up on.  Either one differing per account
        breaks the promise that makes the shared key worth having:
        "what does a brake job cost" has to mean the same thing in
        every fleet.

        Editable here and never overwritten — description, labor
        estimate, vehicle_type and status.  Shop rates and crews
        genuinely differ, so a fleet that knows its brake job takes
        2.5h must be able to say so; the fan-out fills these only
        while they are still blank.
        """
        task = await self.get_service_task(task_id, account_id)
        if not task:
            return False
        allowed = {"description", "expected_labor_hours", "status",
                   "parent_id", "vehicle_type"}
        if not task.get("canonical_key"):
            allowed.update({"name", "system_key"})
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "status" in updates and updates["status"] not in (TASK_ACTIVE, TASK_ARCHIVED):
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
        if "parent_id" in updates:
            pid = updates["parent_id"]
            if pid:
                parent = await self.get_service_task(int(pid), account_id)
                if not parent or parent.get("parent_id") or int(pid) == task_id:
                    return False
            else:
                # 0 is the explicit "no parent" value — a JSON null can't
                # travel here (the router drops None fields, else every
                # unsent field would read as "clear me"), so detaching a
                # subtask needs a value that means something.
                updates["parent_id"] = None
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

    # ── Merge ────────────────────────────────────────────────────────

    async def merge_service_tasks(
        self, account_id: int, loser_id: int, winner_id: int,
    ) -> tuple[bool, str]:
        """Fold a duplicate task into the canonical one.

        The problem this solves is Fleetio's own: "Brake Job", "brake
        job" and "Brakes" typed at three different times split every
        report three ways.  Merging repoints all history onto one task
        so the numbers add up again.

        Rules:
          * the LOSER must be one of the account's own tasks — a
            standard task's ``canonical_key`` is the cross-account
            identity, so it can only ever be archived;
          * one-level nesting survives: a loser with subtasks can't
            fold into a task that is itself a subtask.

        Everything moves in ONE transaction — a half-merged vocabulary
        would be worse than a duplicated one.  Returns ``(ok, reason)``.
        """
        if loser_id == winner_id:
            return False, "A task can't be merged into itself."
        loser = await self.get_service_task(loser_id, account_id)
        winner = await self.get_service_task(winner_id, account_id)
        if not loser or not winner:
            return False, "Task not found."
        if loser.get("canonical_key"):
            return False, (
                "Standard tasks can't be merged away — archive it instead, "
                "or merge your custom task into it."
            )

        children = [
            t for t in await self.list_service_tasks(
                account_id, include_archived=True)
            if t.get("parent_id") and int(t["parent_id"]) == loser_id
        ]
        if children and winner.get("parent_id"):
            return False, (
                "That task has subtasks, and the target is itself a subtask "
                "(only one level of nesting is allowed)."
            )

        winner_value = winner["canonical_key"] or winner["name"]

        async with self.transaction():
            # Legacy string columns first, while the loser's id still
            # identifies its rows — during the dual-write window a stale
            # reader must not see the old name pointing at a dead task.
            await self._db.execute(
                "UPDATE maintenance_tasks SET task_type = ? "
                "WHERE account_id = ? AND service_task_id = ?",
                (winner_value, account_id, loser_id),
            )
            await self._db.execute(
                "UPDATE work_order_labor SET service_task = ? "
                "WHERE account_id = ? AND service_task_id = ?",
                (winner_value, account_id, loser_id),
            )
            # work_order_parts has no account_id — scope via parent WOs.
            await self._db.execute(
                "UPDATE work_order_parts SET service_task = ? "
                "WHERE service_task_id = ? AND work_order_id IN "
                "  (SELECT id FROM work_orders WHERE account_id = ?)",
                (winner_value, loser_id, account_id),
            )

            # Then the references themselves.
            await self._db.execute(
                "UPDATE maintenance_tasks SET service_task_id = ? "
                "WHERE account_id = ? AND service_task_id = ?",
                (winner_id, account_id, loser_id),
            )
            await self._db.execute(
                "UPDATE work_order_labor SET service_task_id = ? "
                "WHERE account_id = ? AND service_task_id = ?",
                (winner_id, account_id, loser_id),
            )
            await self._db.execute(
                "UPDATE work_order_parts SET service_task_id = ? "
                "WHERE service_task_id = ? AND work_order_id IN "
                "  (SELECT id FROM work_orders WHERE account_id = ?)",
                (winner_id, loser_id, account_id),
            )

            # Linked parts: drop the loser's links that the winner
            # already has (UNIQUE(service_task_id, part_id)), move the rest.
            await self._db.execute(
                "DELETE FROM service_task_parts "
                "WHERE account_id = ? AND service_task_id = ? AND part_id IN "
                "  (SELECT part_id FROM service_task_parts "
                "   WHERE account_id = ? AND service_task_id = ?)",
                (account_id, loser_id, account_id, winner_id),
            )
            await self._db.execute(
                "UPDATE service_task_parts SET service_task_id = ? "
                "WHERE account_id = ? AND service_task_id = ?",
                (winner_id, account_id, loser_id),
            )

            # Subtasks follow their parent.
            await self._db.execute(
                "UPDATE service_tasks SET parent_id = ? "
                "WHERE account_id = ? AND parent_id = ?",
                (winner_id, account_id, loser_id),
            )
            await self._db.execute(
                "DELETE FROM service_tasks WHERE id = ? AND account_id = ? "
                "AND canonical_key = ''",
                (loser_id, account_id),
            )
        return True, ""

    # ── Linked parts ─────────────────────────────────────────────────
    #
    # The parts a task normally needs.  This is the reason a task earns
    # its own object: "Replace front brake pads" can carry its usual
    # parts and labor estimate onto a work order instead of being
    # retyped every visit.  Quantities here are DEFAULTS — the invoice
    # is still the truth about what went on the truck.

    async def list_service_task_parts(
        self, service_task_id: int, account_id: int,
    ) -> list[dict[str, Any]]:
        """The task's linked parts, joined to their catalog names."""
        cur = await self._db.execute(
            "SELECT stp.id, stp.part_id, stp.quantity, "
            "       c.name AS part_name, c.part_number "
            "FROM service_task_parts stp "
            "JOIN parts_catalog c ON c.id = stp.part_id "
            "WHERE stp.service_task_id = ? AND stp.account_id = ? "
            "ORDER BY c.name",
            (service_task_id, account_id),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def add_service_task_part(
        self, account_id: int, service_task_id: int, part_id: int,
        *, quantity: float = 1.0,
    ) -> Optional[dict[str, Any]]:
        """Link a catalog part to a task.  Returns None when either side
        doesn't belong to this account (never trust caller ids) or the
        link already exists."""
        task = await self.get_service_task(service_task_id, account_id)
        if not task:
            return None
        cur = await self._db.execute(
            "SELECT id FROM parts_catalog WHERE id = ? AND account_id = ?",
            (part_id, account_id),
        )
        if not await cur.fetchone():
            return None
        cur = await self._db.execute(
            "INSERT INTO service_task_parts "
            "(account_id, service_task_id, part_id, quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (service_task_id, part_id) DO NOTHING "
            "RETURNING id",
            (account_id, service_task_id, part_id,
             max(float(quantity or 1), 0.0) or 1.0, self._now()),
        )
        row = await cur.fetchone()
        await self._db.commit()
        if not row:
            return None
        links = await self.list_service_task_parts(service_task_id, account_id)
        new_id = int(dict(row)["id"])
        return next((l for l in links if int(l["id"]) == new_id), None)

    async def remove_service_task_part(
        self, account_id: int, link_id: int,
    ) -> bool:
        cur = await self._db.execute(
            "DELETE FROM service_task_parts WHERE id = ? AND account_id = ?",
            (link_id, account_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def service_task_defaults(
        self, account_id: int, value: str,
    ) -> Optional[dict[str, Any]]:
        """What picking this task should PRE-FILL — its labor estimate
        and usual parts.

        Read-only and lookup-only: unlike ``resolve_service_task`` this
        does NOT create an archived task for an unknown value, because
        merely asking "what are this task's defaults?" must never write.
        Returns None when the task isn't known.
        """
        raw = (value or "").strip()
        if not raw:
            return None
        cur = await self._db.execute(
            "SELECT * FROM service_tasks "
            "WHERE account_id = ? AND (canonical_key = ? OR name_key = ?)",
            (account_id, raw.lower(), service_task_name_key(raw)),
        )
        row = await cur.fetchone()
        if not row:
            return None
        task = dict(row)
        return {
            "id": task["id"],
            "name": task["name"],
            "expected_labor_hours": float(task.get("expected_labor_hours") or 0),
            "parts": await self.list_service_task_parts(task["id"], account_id),
        }
