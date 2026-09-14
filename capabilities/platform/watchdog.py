"""Deprecated import path — the machinery watchdog moved to ``system.watchdog``.
This name IS that module (``sys.modules`` identity)."""
import sys as _sys
from system import watchdog as _m
_sys.modules[__name__] = _m
