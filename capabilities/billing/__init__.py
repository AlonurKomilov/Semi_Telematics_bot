"""Billing capability — provider protocol and registry.

Usage:
    from capabilities.billing import get_provider
    provider = get_provider()          # returns StubBillingProvider or StripeBillingProvider
    summary = await provider.get_summary(account_id, db)
"""

from __future__ import annotations

import os

from .provider import BillingProvider
from .stub import StubBillingProvider

_provider: BillingProvider | None = None


def get_provider() -> BillingProvider:
    """Return the active billing provider singleton.

    Provider is chosen from BILLING_PROVIDER env var:
      - 'stub'   (default) — no-op, returns zeroed data, safe for dev
      - 'stripe' — requires STRIPE_SECRET_KEY to be set

    The provider is instantiated once and reused.
    """
    global _provider
    if _provider is not None:
        return _provider

    name = os.getenv("BILLING_PROVIDER", "stub").lower()

    if name == "stripe":
        from .stripe_client import StripeBillingProvider
        _provider = StripeBillingProvider()
    else:
        _provider = StubBillingProvider()

    return _provider


__all__ = ["BillingProvider", "get_provider"]
