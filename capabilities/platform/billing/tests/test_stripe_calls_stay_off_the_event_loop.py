"""No Stripe call blocks the worker it is made on.

The SDK is synchronous. Inside an ``async def`` that means the HTTPS
round trip to Stripe freezes the whole event loop — every other request
that worker is serving waits behind it, not just the caller's.

That is not theory: on 2026-09-12 the operator pressed "Create Stripe
price" and both that PUT and the Payment-wiring check the same page had
just issued died together. The save's three Stripe round trips had the
worker to themselves.

Roughly 0.7s per call from this server; a plan save makes three and
every customer checkout makes one or two. So the rule is: a Stripe call
inside async code goes through ``_off_loop``.
"""

from __future__ import annotations

import ast
import re

from capabilities.platform.billing import stripe_client

SOURCE = __import__("pathlib").Path(stripe_client.__file__).read_text()

#: Pure-CPU SDK helpers — no network, so no reason to leave the loop.
#: ``construct_event`` only verifies an HMAC over bytes we already hold.
LOCAL_ONLY = {"Webhook.construct_event"}


def _async_bodies():
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            yield node.name, ast.get_source_segment(SOURCE, node) or ""


def test_every_network_call_leaves_the_loop():
    offenders = []
    for name, body in _async_bodies():
        for m in re.finditer(r"(?<!_off_loop\()\bstripe\.((?:[a-z_]+\.)?[A-Za-z]+\.[a-z_]+)\(", body):
            call = m.group(1)
            if call in LOCAL_ONLY:
                continue
            line = body[:m.start()].rsplit("\n", 1)[-1]
            if "_off_loop(" in line:
                continue
            offenders.append(f"{name}: stripe.{call}(")
    assert not offenders, (
        "these block their worker for a full round trip to Stripe:\n    "
        + "\n    ".join(sorted(set(offenders)))
        + "\n\nWrap them: `await _off_loop(stripe.X.y, …)`."
    )


def test_the_helper_still_actually_leaves_the_loop():
    """A helper that stopped using a thread would pass the check above
    while changing nothing."""
    assert "asyncio.to_thread" in SOURCE
    src = ast.get_source_segment(SOURCE, next(
        n for n in ast.walk(ast.parse(SOURCE))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_off_loop")) or ""
    assert "asyncio.to_thread" in src, "_off_loop no longer runs the call in a thread"


def test_the_price_archiver_is_awaited_not_called():
    """It makes a Stripe call, so it is async, and the route must await
    it — a forgotten await here silently skips archiving the old price."""
    import inspect
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    assert inspect.iscoroutinefunction(StripeBillingProvider.archive_plan_price)
    from tests._repo import REPO
    route = (REPO / "capabilities" / "platform" / "billing" / "operator"
             / "router.py").read_text()
    assert "await provider.archive_plan_price(" in route
