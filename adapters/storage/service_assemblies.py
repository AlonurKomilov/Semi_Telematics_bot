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

from adapters.storage.service_tasks import SYSTEM_KEYS

logger = logging.getLogger("bot.storage")

ASM_ACTIVE = "active"
ASM_ARCHIVED = "archived"

# Task systems that DELEGATE parts spend to the part's assembly —
# they describe activity, not components (plus '' = untagged).
DELEGATING_SYSTEMS = frozenset({"pm", "inspection", "other", ""})


def normalize_assembly_key(value: str) -> str:
    """Lowercase snake key, or '' — same discipline as system keys."""
    v = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return v[:60]


# (key, label, system) — the bootstrap seed, advisor-reviewed
# 2026-07-27.  ~112 entries: coarse enough that a tech actually picks
# one, fine enough that the drill-down says something.
SERVICE_ASSEMBLIES: tuple[tuple[str, str, str], ...] = (
    # ── Engine ──
    ("oil_lubrication",    "Oil & Lubrication",             "engine"),
    ("air_intake",         "Air Intake & Filters",          "engine"),
    ("turbocharger",       "Turbocharger",                  "engine"),
    ("cylinder_head",      "Cylinder Head & Valvetrain",    "engine"),
    ("engine_internals",   "Engine Block, Internals & Mounts", "engine"),
    ("gaskets_seals",      "Gaskets & Seals",               "engine"),
    ("belts_tensioners",   "Belts & Tensioners",            "engine"),
    ("crankcase_vent",     "Crankcase Ventilation",         "engine"),
    ("engine_sensors",     "Engine Sensors & Electronics",  "engine"),
    # ── Cooling ──
    ("radiator",           "Radiator & Surge Tank",         "cooling"),
    ("charge_air_cooler",  "Charge Air Cooler",             "cooling"),
    ("water_pump",         "Water Pump",                    "cooling"),
    ("thermostat",         "Thermostat & Housing",          "cooling"),
    ("fan_clutch",         "Fan & Fan Clutch",              "cooling"),
    ("coolant_hoses",      "Hoses & Piping",                "cooling"),
    ("coolant",            "Coolant & Additives",           "cooling"),
    # ── Fuel ──
    ("fuel_tanks",         "Fuel Tanks",                    "fuel"),
    ("fuel_lines",         "Fuel Lines & Fittings",         "fuel"),
    ("fuel_filters",       "Fuel Filters & Water Separator", "fuel"),
    ("fuel_pump",          "Fuel Pump",                     "fuel"),
    ("injectors",          "Injectors",                     "fuel"),
    ("fuel_sensors",       "Fuel Sensors",                  "fuel"),
    # ── Exhaust & Aftertreatment ──
    ("dpf",                "DPF",                           "exhaust"),
    ("doc",                "DOC",                           "exhaust"),
    ("scr_dosing",         "SCR & DEF Dosing",              "exhaust"),
    ("def_tank",           "DEF Tank & Lines",              "exhaust"),
    ("egr",                "EGR",                           "exhaust"),
    ("exhaust_piping",     "Exhaust Piping & Clamps",       "exhaust"),
    ("nox_temp_sensors",   "NOx & Temp Sensors",            "exhaust"),
    ("muffler_stack",      "Muffler & Stack",               "exhaust"),
    # ── Drivetrain ──
    ("transmission",       "Transmission",                  "drivetrain"),
    ("clutch",             "Clutch",                        "drivetrain"),
    ("driveshaft",         "Driveshaft & U-Joints",         "drivetrain"),
    ("differentials",      "Differentials",                 "drivetrain"),
    ("axle_shafts",        "Axle Shafts",                   "drivetrain"),
    ("pto",                "PTO",                           "drivetrain"),
    ("trans_cooler",       "Transmission Cooler & Lines",   "drivetrain"),
    ("drivetrain_mounts",  "Drivetrain Mounts",             "drivetrain"),
    # ── Brakes ──
    ("pads_shoes",         "Brake Pads & Shoes",            "brakes"),
    ("drums_rotors",       "Drums & Rotors",                "brakes"),
    ("brake_chambers",     "Brake Chambers",                "brakes"),
    ("slack_adjusters",    "Slack Adjusters",               "brakes"),
    ("scam_hardware",      "S-Cam & Hardware",              "brakes"),
    ("abs",                "ABS & Wheel Sensors",           "brakes"),
    ("brake_control_valves", "Brake Control Valves",        "brakes"),
    ("brake_lines",        "Brake Lines & Hoses",           "brakes"),
    ("parking_brake",      "Parking Brake",                 "brakes"),
    # ── Air System ──
    ("air_compressor",     "Air Compressor",                "air_system"),
    ("air_dryer",          "Air Dryer",                     "air_system"),
    ("air_tanks",          "Air Tanks",                     "air_system"),
    ("air_lines",          "Air Lines & Fittings",          "air_system"),
    ("gladhands",          "Gladhands & Trailer Supply",    "air_system"),
    ("air_supply_valves",  "Air Supply Valves & Governor",  "air_system"),
    # ── Suspension ──
    ("leaf_springs",       "Leaf Springs",                  "suspension"),
    ("air_springs",        "Air Springs (Bags)",            "suspension"),
    ("shocks",             "Shock Absorbers",               "suspension"),
    ("torque_rods",        "Torque Rods",                   "suspension"),
    ("bushings_hangers",   "Bushings & Hangers",            "suspension"),
    ("ride_height_valves", "Ride Height Valves",            "suspension"),
    ("sway_bar",           "Sway Bar",                      "suspension"),
    # ── Steering ──
    ("steering_gear",      "Steering Gear",                 "steering"),
    ("ps_pump",            "Power Steering Pump & Lines",   "steering"),
    ("drag_link_tie_rods", "Drag Link & Tie Rods",          "steering"),
    ("steer_kingpins",     "Steer Axle Kingpins & Knuckles", "steering"),
    ("steering_column",    "Steering Column & Wheel",       "steering"),
    # ── Tires & Wheels ──
    ("tires",              "Tires",                         "tires_wheels"),
    ("wheels_rims",        "Wheels & Rims",                 "tires_wheels"),
    ("hubs_bearings",      "Hubs & Bearings",               "tires_wheels"),
    ("wheel_seals",        "Wheel Seals",                   "tires_wheels"),
    ("studs_nuts",         "Studs & Nuts",                  "tires_wheels"),
    ("tpms",               "TPMS",                          "tires_wheels"),
    # ── Electrical ──
    ("batteries",          "Batteries",                     "electrical"),
    ("alternator",         "Alternator",                    "electrical"),
    ("starter",            "Starter",                       "electrical"),
    ("wiring_harnesses",   "Wiring & Harnesses",            "electrical"),
    ("fuses_relays",       "Fuses & Relays",                "electrical"),
    ("ecu_modules",        "ECU & Modules",                 "electrical"),
    ("switches_gauges",    "Switches & Gauges",             "electrical"),
    ("chassis_sensors",    "Chassis Sensors",               "electrical"),
    # ── Lighting (merged per advisor: techs don't split marker/turn) ──
    ("headlights",         "Headlights",                    "lighting"),
    ("exterior_lamps",     "Exterior Lamps",                "lighting"),
    ("work_lights",        "Work Lights & Beacons",         "lighting"),
    ("lighting_wiring",    "Lighting Wiring & Connectors",  "lighting"),
    # ── HVAC ──
    ("ac_compressor",      "A/C Compressor",                "hvac"),
    ("condenser",          "Condenser",                     "hvac"),
    ("evaporator",         "Evaporator & Core",             "hvac"),
    ("blower_fans",        "Blower & Fans",                 "hvac"),
    ("heater_core",        "Heater Core & Valves",          "hvac"),
    ("refrigerant",        "Refrigerant & Charging",        "hvac"),
    ("apu",                "APU & Bunk Heater",             "hvac"),
    ("hvac_controls",      "Controls & Thermostat",         "hvac"),
    # ── Body & Cab (advisor gaps: fifth wheel, wipers) ──
    ("doors_locks",        "Doors & Locks",                 "body_cab"),
    ("windows",            "Windows & Regulators",          "body_cab"),
    ("windshield_glass",   "Windshield & Glass",            "body_cab"),
    ("wipers",             "Wipers & Washer System",        "body_cab"),
    ("mirrors",            "Mirrors",                       "body_cab"),
    ("hood_fenders",       "Hood & Fenders",                "body_cab"),
    ("bumpers",            "Bumpers",                       "body_cab"),
    ("cab_mounts",         "Cab Mounts & Suspension",       "body_cab"),
    ("seats_interior",     "Seats & Interior",              "body_cab"),
    ("paint_decals",       "Paint & Decals",                "body_cab"),
    ("steps_fairings",     "Steps, Fairings & Mud Flaps",   "body_cab"),
    ("fifth_wheel",        "Fifth Wheel & Coupling",        "body_cab"),
    # ── Trailer (trailer-unique structure only; trailer lights →
    #    Lighting, trailer ABS → Brakes, per advisor) ──
    ("landing_gear",       "Landing Gear",                  "trailer"),
    ("trailer_kingpin",    "Kingpin & Upper Coupler",       "trailer"),
    ("trailer_doors",      "Trailer Doors",                 "trailer"),
    ("floor_crossmembers", "Floor & Crossmembers",          "trailer"),
    ("roof_walls",         "Roof & Walls",                  "trailer"),
    ("reefer_unit",        "Reefer Unit",                   "trailer"),
    ("liftgate",           "Liftgate",                      "trailer"),
    ("tarps_straps",       "Tarps & Straps",                "trailer"),
    ("sliding_tandem",     "Sliding Tandem",                "trailer"),
)

# Sanity at import: every assembly parents onto a real system.
for _k, _l, _s in SERVICE_ASSEMBLIES:
    assert _s in SYSTEM_KEYS, f"assembly {_k} parents unknown system {_s}"


# ── Suggesting an assembly from a part's name ───────────────────────
#
# The backfill path for the 1,433 existing parts: keyword suggest, a
# human confirms (per-row chip, or the one-click bulk apply).  Word-
# boundary matching, specific words only, longest match wins — the
# same discipline as the system suggester, for the same reason.
_ASSEMBLY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "oil_lubrication":  ("oil filter", "engine oil", "oil pan", "oil pump",
                         "oil cooler", "dipstick"),
    "air_intake":       ("air filter", "air cleaner", "intake"),
    "turbocharger":     ("turbo", "turbocharger", "wastegate", "actuator"),
    "cylinder_head":    ("head gasket", "valve cover", "rocker", "camshaft",
                         "injector cup"),
    "engine_internals": ("piston", "liner", "crankshaft", "main bearing",
                         "rod bearing", "flywheel", "engine mount", "damper"),
    "gaskets_seals":    ("gasket", "seal kit", "o ring", "oring", "rear main"),
    "belts_tensioners": ("belt", "tensioner", "idler pulley", "serpentine"),
    "crankcase_vent":   ("ccv", "crankcase", "breather"),
    "engine_sensors":   ("cam sensor", "crank sensor", "oil pressure sensor",
                         "map sensor", "boost sensor"),
    "radiator":         ("radiator", "surge tank", "overflow tank"),
    "charge_air_cooler": ("charge air", "cac", "intercooler"),
    "water_pump":       ("water pump",),
    "thermostat":       ("thermostat",),
    "fan_clutch":       ("fan clutch", "fan blade", "fan hub"),
    "coolant_hoses":    ("coolant hose", "radiator hose", "heater hose"),
    "coolant":          ("coolant", "antifreeze"),
    "fuel_tanks":       ("fuel tank", "fuel cap"),
    "fuel_lines":       ("fuel line", "fuel fitting"),
    "fuel_filters":     ("fuel filter", "water separator", "fuel water"),
    "fuel_pump":        ("fuel pump", "lift pump", "transfer pump"),
    "injectors":        ("injector", "injectors"),
    "fuel_sensors":     ("fuel sensor", "fuel level sensor"),
    "dpf":              ("dpf", "particulate filter", "soot"),
    "doc":              ("doc", "oxidation catalyst"),
    "scr_dosing":       ("scr", "def doser", "doser", "def pump",
                         "def injector"),
    "def_tank":         ("def tank", "def line", "def fluid", "def head",
                         "def level", "urea", "adblue"),
    "egr":              ("egr",),
    "exhaust_piping":   ("exhaust pipe", "exhaust clamp", "flex pipe",
                         "bellows"),
    "nox_temp_sensors": ("nox", "exhaust temp", "egt"),
    "muffler_stack":    ("muffler", "stack", "tailpipe"),
    "transmission":     ("transmission", "trans filter", "gearbox",
                         "shift", "synchro"),
    "clutch":           ("clutch", "pressure plate", "throwout",
                         "release bearing"),
    "driveshaft":       ("driveshaft", "drive shaft", "u joint", "u-joint",
                         "yoke", "carrier bearing"),
    "differentials":    ("differential", "diff ", "ring and pinion",
                         "carrier"),
    "axle_shafts":      ("axle shaft", "axle seal"),
    "pto":              ("pto",),
    "trans_cooler":     ("transmission cooler", "trans cooler",
                         "trans line"),
    "drivetrain_mounts": ("transmission mount", "trans mount"),
    "pads_shoes":       ("brake pad", "brake shoe", "pads", "shoes",
                         "lining"),
    "drums_rotors":     ("drum", "rotor", "disc"),
    "brake_chambers":   ("brake chamber", "chamber", "diaphragm"),
    "slack_adjusters":  ("slack adjuster", "slack"),
    "scam_hardware":    ("s cam", "s-cam", "camshaft brake", "anchor pin",
                         "brake hardware", "spider"),
    "abs":              ("abs", "wheel speed sensor", "tone ring"),
    "brake_control_valves": ("brake valve", "relay valve", "quick release",
                             "foot valve", "spring brake valve"),
    "brake_lines":      ("brake line", "brake hose"),
    "parking_brake":    ("parking brake", "park brake"),
    "air_compressor":   ("air compressor", "compressor head"),
    "air_dryer":        ("air dryer", "dryer cartridge", "desiccant"),
    "air_tanks":        ("air tank", "purge tank"),
    "air_lines":        ("air line", "air fitting", "dot tubing"),
    "gladhands":        ("gladhand", "glad hand", "trailer supply",
                         "tractor protection"),
    "air_supply_valves": ("governor", "unloader", "pressure protection"),
    "leaf_springs":     ("leaf spring", "spring pack", "spring pin",
                         "shackle"),
    "air_springs":      ("air spring", "air bag", "airbag", "bellow"),
    "shocks":           ("shock", "shocks", "shock absorber"),
    "torque_rods":      ("torque rod", "torque arm", "radius rod"),
    "bushings_hangers": ("bushing", "hanger", "equalizer"),
    "ride_height_valves": ("ride height", "leveling valve", "height control"),
    "sway_bar":         ("sway bar", "stabilizer bar"),
    "steering_gear":    ("steering gear", "steering box", "pitman"),
    "ps_pump":          ("power steering", "ps pump", "steering hose"),
    "drag_link_tie_rods": ("drag link", "tie rod", "tie-rod"),
    "steer_kingpins":   ("kingpin", "king pin", "knuckle"),
    "steering_column":  ("steering column", "steering wheel",
                         "steering shaft"),
    "tires":            ("tire", "tyre", "recap", "retread", "casing"),
    "wheels_rims":      ("wheel", "rim"),
    "hubs_bearings":    ("hub", "bearing", "spindle nut"),
    "wheel_seals":      ("wheel seal", "hub seal", "axle seal"),
    "studs_nuts":       ("stud", "lug nut", "lug"),
    "tpms":             ("tpms", "tire sensor"),
    "batteries":        ("battery", "batteries", "battery cable",
                         "battery box"),
    "alternator":       ("alternator",),
    "starter":          ("starter", "solenoid"),
    "wiring_harnesses": ("harness", "wiring", "wire", "pigtail",
                         "connector"),
    "fuses_relays":     ("fuse", "relay", "breaker"),
    "ecu_modules":      ("ecu", "ecm", "module", "bcm"),
    "switches_gauges":  ("switch", "gauge", "cluster"),
    "chassis_sensors":  ("speed sensor", "temp sensor", "pressure sensor"),
    "headlights":       ("headlight", "headlamp"),
    "exterior_lamps":   ("tail light", "taillight", "marker", "clearance",
                         "turn signal", "lamp", "light bar"),
    "work_lights":      ("work light", "beacon", "strobe", "spotlight"),
    "lighting_wiring":  ("light wiring", "light harness", "light plug",
                         "7 way", "7-way"),
    "ac_compressor":    ("ac compressor", "a/c compressor",
                         "air conditioning compressor"),
    "condenser":        ("condenser",),
    "evaporator":       ("evaporator",),
    "blower_fans":      ("blower", "blower motor"),
    "heater_core":      ("heater core", "heater valve"),
    "refrigerant":      ("refrigerant", "freon", "r134", "r-134",
                         "1234yf", "recharge"),
    "apu":              ("apu", "bunk heater", "espar", "webasto"),
    "hvac_controls":    ("hvac control", "climate control", "ac control"),
    "doors_locks":      ("door", "lock", "latch", "hinge", "door handle"),
    "windows":          ("window", "regulator"),
    "windshield_glass": ("windshield", "glass", "chip repair"),
    "wipers":           ("wiper", "washer pump", "washer fluid",
                         "wiper motor"),
    "mirrors":          ("mirror",),
    "hood_fenders":     ("hood", "fender", "grille", "grill"),
    "bumpers":          ("bumper",),
    "cab_mounts":       ("cab mount", "cab shock", "cab airbag"),
    "seats_interior":   ("seat", "dash", "upholstery", "mattress",
                         "floor mat"),
    "paint_decals":     ("paint", "decal", "vinyl", "lettering"),
    "steps_fairings":   ("step", "fairing", "mud flap", "mudflap",
                         "deck plate", "catwalk"),
    "fifth_wheel":      ("fifth wheel", "5th wheel", "jaw kit",
                         "slide plate"),
    "landing_gear":     ("landing gear", "landing leg", "crank handle",
                         "sand shoe"),
    "trailer_kingpin":  ("upper coupler",),
    "trailer_doors":    ("roll door", "swing door", "roll up door",
                         "door roller", "door cable", "door spring",
                         "door panel"),
    "floor_crossmembers": ("crossmember", "cross member", "trailer floor",
                           "floor board", "decking"),
    "roof_walls":       ("trailer roof", "roof bow", "side panel",
                         "wall panel", "front wall"),
    "reefer_unit":      ("reefer", "thermo king", "thermoking", "carrier unit",
                         "refrigeration unit"),
    "liftgate":         ("liftgate", "lift gate", "tuckaway"),
    "tarps_straps":     ("tarp", "strap", "winch", "bungee"),
    "sliding_tandem":   ("tandem", "slider", "pin puller"),
}


def suggest_assembly_for(name: str) -> str:
    """Best-guess assembly key for a part NAME, or ''.  A suggestion —
    a human confirms before anything is written."""
    hay = " ".join((name or "").lower().split())
    if not hay:
        return ""
    best_key, best_len = "", 0
    for key, words in _ASSEMBLY_KEYWORDS.items():
        for w in words:
            if len(w) > best_len and re.search(rf"\b{re.escape(w)}\b", hay):
                best_key, best_len = key, len(w)
    return best_key


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
