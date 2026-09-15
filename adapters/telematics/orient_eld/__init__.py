"""ORIENT ELD telematics provider.

Importing this module registers ``OrientEldProvider`` with the
telematics registry under ``provider_id="orient_eld"``.

ORIENT ELD is the platform's second electronic logging device, and the
first one that is not Samsara — which makes it the first real test of
the claim ``features/eld/tests/test_eld_is_provider_agnostic.py`` was
written to prove.  Nothing under ``features/`` changed to add it.
"""

from __future__ import annotations

from .client import (  # noqa: F401
    MultiCompanyOrientClient,
    ORIENT_BASE_URL,
    OrientEldClient,
    build_multi_company_orient_client,
    utc_iso,
)
from .provider import OrientEldProvider

from adapters.telematics.registry import register_provider as _register

_register("orient_eld", OrientEldProvider)


__all__ = [
    "MultiCompanyOrientClient",
    "ORIENT_BASE_URL",
    "OrientEldClient",
    "OrientEldProvider",
    "build_multi_company_orient_client",
    "utc_iso",
]
