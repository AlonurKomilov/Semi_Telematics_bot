"""Signal collectors — one module per contributing feature.

Each collector returns a per-subject dict of normalized facts.  The
service layer merges them and feeds the result to the engine.

``SIGNALS`` is the hub's extension point: the Scoring hub discovers its
per-feature contributions through this map (mirroring the alert-source
and AI-tool registries).  Signal modules keep their own call shapes
(``aggregate`` / ``from_*`` helpers differ per data source on purpose),
so the registry maps key → module, not key → one function.  Adding a
signal = add the module + one entry here.
"""

from features.vehicles.cameras import signal as cameras
from features.vehicles.efficiency import signal as efficiency
from features.vehicles.fuel import signal as fuel
from . import maintenance
from features.events import signal as events
from features.vehicles.efficiency import vehicle_signal as vehicle_efficiency
from features.vehicles.health import signal as vehicle_health

SIGNALS = {
    "cameras": cameras,
    "efficiency": efficiency,
    "fuel": fuel,
    "maintenance": maintenance,
    "events": events,
    "vehicle_efficiency": vehicle_efficiency,
    "vehicle_health": vehicle_health,
}
