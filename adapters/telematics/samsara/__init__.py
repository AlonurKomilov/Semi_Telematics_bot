"""Samsara telematics provider.

Importing this module registers ``SamsaraProvider`` with the
telematics registry under ``provider_id="samsara"``.  Other code that
needs a Samsara-specific affordance (the underlying client, the
circuit breaker, the multi-company builder) should import from the
direct submodule paths — those are stable.

Backward compatibility: the legacy import path ``adapters.samsara.*``
remains valid via re-export shims so existing callers don't need to
move.  New code should import from ``adapters.telematics.samsara.*``
directly.
"""

from __future__ import annotations

# Re-export the most commonly imported public surface for ergonomic
# ``from adapters.telematics.samsara import ...`` access.
from .client import (  # noqa: F401
    COMPANY_DISPLAY,
    MultiCompanyClient,
    ORG_IDS,
    SamsaraClient,
    SamsaraPermissionError,
    build_multi_company_client,
    populate_company_display,
)
from .circuit_breaker import samsara_breaker, SamsaraUnavailable  # noqa: F401
from .provider import SamsaraProvider

# Auto-register with the telematics registry.  Idempotent — calling
# more than once is a no-op replace (logged at WARNING in the registry).
from adapters.telematics.registry import register_provider as _register

_register("samsara", SamsaraProvider)


__all__ = [
    "COMPANY_DISPLAY",
    "MultiCompanyClient",
    "ORG_IDS",
    "SamsaraClient",
    "SamsaraPermissionError",
    "SamsaraProvider",
    "SamsaraUnavailable",
    "build_multi_company_client",
    "populate_company_display",
    "samsara_breaker",
]
