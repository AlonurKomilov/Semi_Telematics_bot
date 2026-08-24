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

from .models import Callout, callout_wire
from .registry import (
    CalloutSpec,
    get_spec,
    known_keys,
    register_callout,
)

__all__ = [
    "Callout",
    "CalloutSpec",
    "callout_wire",
    "get_spec",
    "known_keys",
    "register_callout",
]
