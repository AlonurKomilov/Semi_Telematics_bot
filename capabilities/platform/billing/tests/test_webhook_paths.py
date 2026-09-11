"""The Stripe webhook answers on the provider-named path, and the old one.

The owner registered Stripe against ``/billing/stripe/webhook`` so a
second provider tomorrow gets its own path instead of a switch inside
one handler.  ``/billing/webhook`` stays as an alias: an endpoint that
was registered against it keeps delivering.  Both must also be open to
an account under billing enforcement — a past-due customer's payment
event is exactly the one that must get through.
"""

from __future__ import annotations

from capabilities.platform.billing.router import router


def _routes_by_path():
    return {r.path: r for r in router.routes if hasattr(r, "endpoint")}


def test_both_paths_reach_the_same_handler():
    by_path = _routes_by_path()
    assert "/billing/stripe/webhook" in by_path, "the provider-named path Stripe is configured with"
    assert "/billing/webhook" in by_path, "the alias older endpoints were registered against"
    assert by_path["/billing/stripe/webhook"].endpoint is by_path["/billing/webhook"].endpoint
    assert by_path["/billing/stripe/webhook"].methods == {"POST"}


def test_both_paths_bypass_billing_enforcement():
    from interfaces.api.app import _ENFORCEMENT_BYPASS_SUFFIXES as suffixes
    assert "/billing/webhook" in suffixes
    assert "/billing/stripe/webhook" in suffixes
