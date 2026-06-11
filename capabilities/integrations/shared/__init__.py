"""Provider-agnostic integration surface.

Helpers + routes that EVERY provider gets for free:
  * catalog browse + per-account integration list
  * connect / disconnect / toggles
  * generic ``test-connection`` (delegates to the provider's own probe)
  * effective cadence read

Provider-specific surfaces live in sibling packages
(``capabilities.integrations.samsara``, ``capabilities.integrations.datatruck``)
and never leak their concerns into this one.
"""

from __future__ import annotations
