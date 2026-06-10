"""Backward-compatibility shim — module moved to
``adapters/telematics/samsara/client.py``.

Re-exports the entire public surface so legacy imports continue to
resolve unchanged.  See ``adapters/samsara/__init__.py`` for the
rationale and migration guidance.
"""

from __future__ import annotations

from adapters.telematics.samsara.client import *  # noqa: F401,F403
# Star-imports skip names that start with an underscore.  Re-export
# the private helpers + module-level state callers actually depend on.
from adapters.telematics.samsara.client import (  # noqa: F401
    COMPANY_DISPLAY,
    ORG_IDS,
)
