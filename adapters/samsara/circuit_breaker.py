"""Backward-compatibility shim — module moved to
``adapters/telematics/samsara/circuit_breaker.py``.

Re-exports the entire public surface so legacy imports continue to
resolve unchanged.
"""

from __future__ import annotations

from adapters.telematics.samsara.circuit_breaker import *  # noqa: F401,F403
from adapters.telematics.samsara.circuit_breaker import samsara_breaker  # noqa: F401
