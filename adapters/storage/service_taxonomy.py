"""The repair taxonomy: System (L1) → Assembly (L2) → Part (L3).

One home for the classification axes, because they are ONE hierarchy
even though the columns that carry them sit on different entities
(``service_tasks.system_key``, ``parts_catalog.assembly_key``) and are
served by different features.

Why this module exists rather than the vocabulary living beside its
biggest consumer:

  * The systems tuple used to sit in ``service_tasks.py``, so anything
    else classifying against it — parts, via their assembly; the
    work-order spend reports; an Inspections defect breakdown later —
    imported the shared reporting axis from a module named after ONE
    of its consumers.  ``service_assemblies`` importing ``SYSTEM_KEYS``
    from ``service_tasks`` inverted the dependency outright.

  * THE DELEGATION RULE existed in four places: ``DELEGATING_SYSTEMS``
    here, and three hand-written ``IN ('pm', 'inspection', 'other',
    '')`` literals in ``work_orders.py``.  Adding a fifth delegating
    system would have left the SQL silently stale and misfiled spend
    with no error anywhere.  ``system_rollup_case()`` now generates
    that predicate from the frozenset, so there is one source.

Imports NOTHING from ``service_tasks`` or ``service_assemblies`` — a
back-import would recreate the cycle this module exists to break.

What deliberately did NOT move here: the storage mixins (DB access
belongs with its entity), ``STANDARD_SERVICE_TASKS`` and
``_STANDARD_SYSTEMS`` (the seeded task list is a task concern, not a
taxonomy one), ``service_task_name_key`` and
``system_bucket_collision`` (task-naming rules), and ``ASM_ACTIVE`` /
``ASM_ARCHIVED`` (assembly row lifecycle).
"""

from __future__ import annotations

import re

# The two blocks below came from modules that spelled this import
# differently; both names bind the SAME module, so each moved helper
# stays byte-identical to the version it replaced.
_re = re

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
VEHICLE_SYSTEMS: tuple[dict[str, str], ...] = (
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

#: Deprecated alias kept for the existing import sites — the SAME
#: object, not a copy.  The domain noun is the vehicle system; the old
#: name said it belonged to service tasks, which is what sent the
#: taxonomy to live in that module in the first place.
SERVICE_TASK_SYSTEMS = VEHICLE_SYSTEMS

SYSTEM_KEYS = frozenset(s["key"] for s in VEHICLE_SYSTEMS)
SYSTEM_LABELS = {s["key"]: s["label"] for s in VEHICLE_SYSTEMS}


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
    "work_lights":      ("work light", "beacon", "strobe", "tour"),
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


def system_rollup_case(task_system: str, assembly_system: str) -> str:
    """SQL CASE implementing THE DELEGATION RULE (owner-binding).

    Labor and parts on a COMPONENT-system task follow the task's own
    system — a radiator hose bought during a brake job is brake spend.
    Parts on an ACTIVITY-system task (PM, inspection, other, untagged)
    delegate to their assembly's system instead, so a PM oil filter
    counts as Engine rather than leaving PM fleets with a single
    undifferentiated bar.  A part with no assembly stays on the task.

    Generated from :data:`DELEGATING_SYSTEMS` rather than written out,
    because it is needed in three places and hand-copied lists drift
    silently: a stale copy misfiles spend without raising anything.

    Both arguments are COLUMN EXPRESSIONS supplied by call sites in
    this package, never user input; the IN-list is rendered from a
    frozen internal constant, so there is nothing here to inject.
    """
    delegating = ", ".join(f"'{k}'" for k in sorted(DELEGATING_SYSTEMS))
    # NOTE the two subtleties, both load-bearing and both preserved from
    # the hand-written SQL this replaced:
    #   * ``IS NOT NULL`` — not ``<> ''``.  The assembly join is a LEFT
    #     JOIN, so a part with no assembly yields NULL and must stay on
    #     the task's system rather than delegating to nothing.
    #   * the ELSE coalesces — an untagged line's task system is NULL,
    #     and the reports bucket '' (Unassigned), not NULL.
    return (f"CASE WHEN COALESCE({task_system}, '') IN ({delegating}) "
            f"          AND {assembly_system} IS NOT NULL "
            f"     THEN {assembly_system} "
            f"     ELSE COALESCE({task_system}, '') END")
