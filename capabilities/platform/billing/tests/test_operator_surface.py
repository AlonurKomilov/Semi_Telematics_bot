"""The operator's half of billing, and the URLs it must keep.

Twenty endpoints moved out of ``interfaces/api/routes/system.py`` into
this domain.  The whole value of the move is that it was invisible from
outside: the operator console is a separate SPA on its own deploy
cadence, so a path that shifted would break a console nobody rebuilt
that day, and it would break it silently — a 404 on a page that used to
work.

So this pins the contract the move promised: every money path still
answers under ``/system``, and it answers from the billing domain.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest

#: Every path the operator console calls for money, as it called them
#: before the move.  Written out rather than derived — a list generated
#: from the router would agree with itself no matter where it moved.
MONEY_PATHS = {
    ("GET",    "/api/system/plans"),
    ("POST",   "/api/system/plans"),
    ("PUT",    "/api/system/plans/{tier}"),
    ("GET",    "/api/system/plans/stripe-check"),
    ("POST",   "/api/system/plans/{tier}/offers"),
    ("DELETE", "/api/system/plans/{tier}/offers/{account_id}"),
    ("GET",    "/api/system/plans/{tier}/rollout"),
    ("POST",   "/api/system/plans/{tier}/rollout"),
    ("GET",    "/api/system/plan-requests"),
    ("POST",   "/api/system/plan-requests/{request_id}/status"),
    ("GET",    "/api/system/accounts/{account_id}/discount"),
    ("POST",   "/api/system/accounts/{account_id}/discount"),
    ("DELETE", "/api/system/accounts/{account_id}/discount"),
    ("PATCH",  "/api/system/accounts/{account_id}/plan"),
    ("POST",   "/api/system/accounts/{account_id}/invoice"),
    ("POST",   "/api/system/accounts/{account_id}/sync-quantity"),
    ("PATCH",  "/api/system/accounts/{account_id}/billing-email"),
    ("GET",    "/api/system/invoices"),
    ("GET",    "/api/system/billing-mode"),
    ("GET",    "/api/system/audit/comp-actions"),
}


@pytest.fixture(scope="module")
def mounted():
    from interfaces.api.app import create_api
    app = create_api()
    return {(m, r.path) for r in app.routes
            for m in (getattr(r, "methods", None) or ())}


def test_every_money_path_still_answers_where_the_console_calls_it(mounted):
    missing = sorted(p for p in MONEY_PATHS if p not in mounted)
    assert not missing, (
        "these operator money paths are no longer mounted — the console "
        "calls them and would 404:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in missing))


def test_they_are_served_by_the_billing_domain_not_the_generic_router(mounted):
    """The point of the move.  If these ever answer from system.py again
    the URLs would still pass the test above while the file they live in
    quietly went back to six domains."""
    from capabilities.platform.billing.operator import router as operator_router
    theirs = {(m, "/api" + r.path) for r in operator_router.routes
              for m in (getattr(r, "methods", None) or ())}
    orphans = sorted(p for p in MONEY_PATHS if p not in theirs)
    assert not orphans, (
        "mounted, but not from capabilities/platform/billing/operator/:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in orphans))


def test_the_customer_half_stayed_where_it_was(mounted):
    """The move touched one audience.  A customer paying their own bill
    must not have noticed anything at all."""
    for path in ("/api/billing/summary", "/api/billing/plans",
                 "/api/billing/checkout", "/api/billing/invoices"):
        assert any(p == path for _, p in mounted), f"{path} went missing"
