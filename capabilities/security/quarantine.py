"""Deprecated import path — see capabilities/security/__init__.py.
The real module is system.security.quarantine; this name IS that object."""
import sys as _sys
from system.security import quarantine as _m
_sys.modules[__name__] = _m
