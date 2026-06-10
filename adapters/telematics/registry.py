"""Provider registry.

A module-level mapping of ``provider_id`` to the class that
implements ``TelematicsProvider`` for that vendor.  The scheduler,
capabilities layer, and dashboard all resolve providers through this
registry rather than importing vendor modules directly — which is
what makes future providers slot in without touching consumers.

Registration happens at import time.  Each vendor module's
``__init__.py`` calls ``register_provider`` so that importing
``adapters.telematics`` lazily wires up every provider.  Tests can
also register fakes for isolation.

The registry is process-local.  In multi-worker deployments every
worker initialises its own copy — that's intentional, since the
mapping is constant for the life of the process.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


_registry: dict[str, type] = {}
_lock = threading.Lock()


def register_provider(provider_id: str, cls: type) -> None:
    """Register a provider implementation for ``provider_id``.

    Calling twice with the same ``provider_id`` REPLACES the entry —
    useful in tests but logged at WARNING in production so accidental
    double-registration is easy to spot.
    """
    with _lock:
        if provider_id in _registry:
            existing = _registry[provider_id].__qualname__
            incoming = cls.__qualname__
            if existing != incoming:
                logger.warning(
                    "register_provider: replacing %s with %s for provider_id=%s",
                    existing, incoming, provider_id,
                )
        _registry[provider_id] = cls


def get_provider(provider_id: str) -> type:
    """Resolve a provider class by id.

    Raises ``KeyError`` when no provider is registered — the dashboard
    catches that and renders "provider not available" for an entry
    whose catalog says AVAILABLE but whose registry says otherwise
    (a misconfiguration the audit script in M8 will catch).
    """
    cls = _registry.get(provider_id)
    if cls is None:
        raise KeyError(f"telematics provider not registered: {provider_id!r}")
    return cls


def is_registered(provider_id: str) -> bool:
    """Cheap check the dashboard uses to gate "Connect" buttons.

    A catalog entry whose ``provider_id`` is not registered renders
    as "Coming soon" regardless of its catalog status.
    """
    return provider_id in _registry


def list_registered_providers() -> list[str]:
    """All currently-registered provider ids, sorted for deterministic
    output.  Used by the operator console and the audit script."""
    return sorted(_registry.keys())


def _reset_for_tests() -> None:
    """Clear the registry — tests only.  Production code never calls
    this; pytest fixtures use it to undo monkeypatched registrations
    between cases."""
    with _lock:
        _registry.clear()
