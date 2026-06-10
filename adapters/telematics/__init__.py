"""Telematics integration umbrella.

Houses the vendor-neutral protocol every telematics provider
implements, the registry that maps ``provider_id`` strings to
implementation classes, and the catalog of provider metadata the
dashboard renders.

Today's only registered provider is Samsara (re-exported from
``adapters.samsara`` until the source files relocate under
``adapters/telematics/samsara/``).  Future providers (Motive, Geotab,
Datatruck) plug in by registering against this module without any
changes to consumers — scheduler jobs, capabilities, dashboard routes
all go through the registry rather than importing a vendor module
directly.
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

__all__ = [
    "Capability",
    "ConnectionStatus",
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
