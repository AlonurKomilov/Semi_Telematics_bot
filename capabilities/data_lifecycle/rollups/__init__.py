"""Roll-up hub — generic time-series downsampling (live → 5-min → hourly →
daily → weekly).

Part of the data-lifecycle family (``capabilities/data_lifecycle``); holds only the
registry + engine — each feature owns its cascade + aggregation in its own
module.  ``discover()`` imports the contributors so their ``register_cascade``
calls fire (built from the family's shared ``make_discover``).
"""

from __future__ import annotations

from .._common import make_discover

# Modules that register a roll-up cascade.  Each owns the stream it downsamples
# + its aggregation.  Add a line when a new high-frequency stream needs tiered
# history; missing modules are skipped (logged), never fatal.
_CONTRIBUTORS = (
    "features.vehicles.lifecycle",   # vehicle state → 5-min → hourly → daily → weekly
)

discover = make_discover(_CONTRIBUTORS)
