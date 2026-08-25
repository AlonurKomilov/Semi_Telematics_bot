"""Callouts — persistent, in-place statements pinned to a surface.

A callout is what a screen says about ITSELF: "these miles are summed
across two devices", "this truck has sent no engine data since May 12".
It is pulled with the data it qualifies, lives exactly as long as the
condition is true, and clears itself.

Not to be confused with its neighbours:

  * an **alert** (``capabilities/alerting``) is PUSHED to a person —
    delivery, acknowledgment, an inbox.  A callout is never a delivery
    channel; if a fact also warrants waking someone, the alerting
    pipeline raises that separately.
  * a **banner** (``components/banners`` on the dashboard) is transient
    chrome — it counts down and vanishes.  A callout survives a reload
    because the thing it describes is still true.

Layering, mirroring ``activity_trail``: this package owns the contract
and the key vocabulary; each feature declares its own keys and detects
its own facts in ``features/<x>/callouts.py``.  Capabilities never
import features — the feature registers itself when its module loads.
"""

from capabilities.data_lifecycle._common import make_discover

from .models import Callout, callout_id, callout_wire
from .registry import (
    CalloutSpec,
    ConditionDetector,
    condition_detectors,
    get_spec,
    known_keys,
    register_callout,
    register_detector,
)

# Modules that declare callout keys and how to detect them.  Imported
# by NAME so this capability never imports a feature — the layer rule
# the boundary test enforces.  Add a line when a feature starts
# emitting callouts; a missing module is logged, never fatal.
_CONTRIBUTORS = (
    "features.vehicles.callouts",   # no-engine-data + the mileage caveats
)

discover = make_discover(_CONTRIBUTORS)

__all__ = [
    "Callout",
    "CalloutSpec",
    "ConditionDetector",
    "callout_id",
    "callout_wire",
    "condition_detectors",
    "discover",
    "get_spec",
    "known_keys",
    "register_callout",
    "register_detector",
]
