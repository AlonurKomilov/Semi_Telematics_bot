"""Billing capability — provider protocol and registry.

NAMING: Billing is the platform money FAMILY — everything about 4truck
charging the customer account (subscription, invoices, comp, enforcement;
future: payments, one-time purchases go HERE as children).  Customers see
it labelled "Subscription".  Customer→broker money is a future
`features/invoicing/`; driver pay is `features/payroll/` — never here.
SSOT: docs/FEATURES.md "Money domains".

Usage:
    from capabilities.platform.billing import get_provider
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
        # Refuse to boot in stripe mode without the webhook secret — an
        # unsigned webhook endpoint lets anyone who can reach it forge
        # subscription state (flip an account active, mark a paying
        # customer past_due).  The secret key is checked lazily by
        # _stripe() the first time it's used.
        if not os.getenv("STRIPE_WEBHOOK_SECRET", "").strip():
            raise RuntimeError(
                "BILLING_PROVIDER=stripe but STRIPE_WEBHOOK_SECRET is "
                "not set.  Get the value from Stripe Dashboard → "
                "Developers → Webhooks → (your endpoint) → Signing "
                "secret, then add it to .env."
            )
        from .stripe_client import StripeBillingProvider
        _provider = StripeBillingProvider()
    else:
        _provider = StubBillingProvider()

    return _provider


__all__ = ["BillingProvider", "get_provider"]
