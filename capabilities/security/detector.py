"""Deprecated import path — see capabilities/security/__init__.py.
The real module is system.security.detector; this name IS that object."""
import sys as _sys
from system.security import detector as _m
_sys.modules[__name__] = _m
