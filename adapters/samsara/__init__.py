"""Backward-compatibility shim for the legacy ``adapters.samsara``
import path.

The Samsara client moved under the telematics umbrella at
``adapters/telematics/samsara/``.  Every existing import like
``from adapters.samsara.client import MultiCompanyClient`` keeps
working through this shim — the symbols are re-exported here so no
caller needs to change.

New code should import from ``adapters.telematics.samsara.*`` (or the
provider registry) directly.  This shim is kept indefinitely for
external integrations and any callers we haven't migrated yet.
"""

from __future__ import annotations

# Importing the new location triggers provider auto-registration —
# anyone who imports this legacy path also picks up the registration
# side-effect, so behaviour is identical to importing the new path.
from adapters.telematics.samsara import *  # noqa: F401,F403
