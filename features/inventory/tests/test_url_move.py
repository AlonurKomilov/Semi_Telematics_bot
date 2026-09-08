"""The move to /inventory kept every old address answering.

A URL is a wire identifier: the dashboard calls it, the audit trail
records it, a bookmark holds it, and an installed browser extension may
still ask for it.  So the feature's move out of Vehicles carried its
addresses across rather than replacing them — a new primary under
``/inventory``, the pre-move ``/vehicles/…`` paths kept as deprecated
aliases on the same handlers.

This walks the pairs and proves they are the same route, not two
implementations that will drift.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

# (canonical, legacy) — every route the feature answers on.
PAIRS = [
    ("/inventory/vehicle/{vehicle_name}", "/vehicles/{vehicle_name}/inventory"),
    ("/inventory/all",                    "/vehicles/inventory/all"),
    ("/inventory/alerts",                 "/vehicles/inventory/alerts"),
    ("/inventory/items/{item_id}",        "/vehicles/inventory/{item_id}"),
    ("/inventory/items/{item_id}/verify", "/vehicles/inventory/{item_id}/verify"),
    ("/inventory/items/{item_id}/transfer", "/vehicles/inventory/{item_id}/transfer"),
    ("/inventory/items/{item_id}/remove", "/vehicles/inventory/{item_id}/remove"),
    ("/inventory/items/{item_id}/events", "/vehicles/inventory/{item_id}/events"),
]


def _routes(app):
    """path → {method: endpoint function} for this app."""
    out: dict[str, dict[str, object]] = {}
    for r in app.routes:
        for m in getattr(r, "methods", ()) or ():
            out.setdefault(r.path, {})[m] = getattr(r, "endpoint", None)
    return out


async def test_every_old_address_still_answers_and_is_the_same_handler(api_app):
    app, _ = api_app
    routes = _routes(app)
    for prefix in ("/api", "/api/v1"):
        for canonical, legacy in PAIRS:
            c, l = prefix + canonical, prefix + legacy
            assert c in routes, f"canonical route missing: {c}"
            assert l in routes, f"deprecated alias missing: {l}"
            # Same function object — not a copy that can drift, and not a
            # redirect that would drop a POST body.
            for method, fn in routes[c].items():
                assert routes[l].get(method) is fn, (
                    f"{method} {l} is not the same handler as {method} {c}")


async def test_the_documentation_offers_only_one_way_in(api_app):
    """Both work; only the canonical one is advertised.  An alias in the
    schema is an invitation to write new code against the old name."""
    app, _ = api_app
    schema = app.openapi()
    paths = set(schema["paths"])
    assert any(p.endswith("/inventory/all") for p in paths), "canonical route is undocumented"
    for _, legacy in PAIRS:
        assert not any(p.endswith(legacy) for p in paths), (
            f"the deprecated alias {legacy} is in the OpenAPI schema")
