"""Telematics integration umbrella.

Houses the vendor-neutral protocol every telematics provider
implements, the registry that maps ``provider_id`` strings to
implementation classes, and the catalog of provider metadata the
dashboard renders.

Registered today: Samsara (telematics), Datatruck (TMS) and ORIENT
ELD (electronic logging device).  A provider plugs in by registering
against this module without any change to consumers — scheduler
jobs, capabilities and dashboard routes all go through the registry
rather than importing a vendor module directly.  ORIENT ELD was the
first one added after that claim was made, and it cost no change
under ``features/``.
"""

from __future__ import annotations

from .catalog import (
    PROVIDER_CATALOG,
    ProviderCatalogEntry,
    ProviderStatus,
    resolve_capability_cadence,
)
from .protocol import (
    Capability,
    ConnectionStatus,
    HosClock,
    TelematicsProvider,
)
from .registry import (
    get_provider,
    is_registered,
    list_registered_providers,
    register_provider,
)

# Importing the samsara submodule auto-registers SamsaraProvider in the
# registry.  Future providers (Motive, Geotab, Datatruck) get imported
# here too — one line per provider — so that touching this umbrella
# module is enough to wire every available vendor in.
from . import samsara as _samsara_provider  # noqa: F401
from . import datatruck as _datatruck_provider  # noqa: F401
from . import orient_eld as _orient_eld_provider  # noqa: F401

__all__ = [
    "Capability",
    "ConnectionStatus",
    "HosClock",
    "PROVIDER_CATALOG",
    "ProviderCatalogEntry",
    "ProviderStatus",
    "TelematicsProvider",
    "get_provider",
    "is_registered",
    "list_registered_providers",
    "register_provider",
    "resolve_capability_cadence",
]
