"""A per-company API key is not a Samsara idea, and the routes say so.

The dashboard has always called these through a ``{providerId}``
template; only the backend hardcoded ``/samsara/``.  The second
provider that issues one key per company found that out by getting a
404 on a card that had rendered perfectly.

The generic family now covers every other provider — and is mounted
LAST so Samsara keeps its own handlers.  That ordering is the whole
safety of the change and it fails silently if reversed: same paths,
same response shape, one missing dual-write to the legacy
``companies.samsara_api_key`` column.  Nothing would raise; keys set
through the card would simply stop reaching the column the legacy read
sites still use.

So the precedence is pinned here by NAME of the winning handler, which
is the only thing that actually changes when the include order does.
"""

from __future__ import annotations

import pytest

from capabilities.integrations.router import router


def _winner(method: str, path: str) -> str:
    for r in router.routes:
        if not hasattr(r, "methods") or method not in r.methods:
            continue
        if r.path_regex.match(path):
            return r.endpoint.__name__
    return ""


# ── Samsara keeps its own handlers ────────────────────────────────

@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/integrations/samsara/companies",
     "list_provider_companies"),
    ("PUT", "/integrations/samsara/companies/ACME/credentials",
     "set_company_credential_endpoint"),
    ("DELETE", "/integrations/samsara/companies/ACME/credentials",
     "remove_company_credential_endpoint"),
    ("POST", "/integrations/samsara/companies/ACME/actions/test",
     "test_company_connection_action"),
])
def test_the_generic_family_does_not_shadow_samsara(method, path, expected):
    assert _winner(method, path) == expected, (
        "the generic per-company routes are winning Samsara's paths — "
        "check the include order in capabilities/integrations/router.py; "
        "the generic router must be mounted LAST.  Symptom in production: "
        "per-company keys stop reaching companies.samsara_api_key and "
        "nothing errors."
    )


# ── Every other per-company provider gets the generic family ──────

@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/integrations/orient_eld/companies",
     "list_provider_companies_generic"),
    ("PUT", "/integrations/orient_eld/companies/ACME/credentials",
     "set_company_credential_generic"),
    ("DELETE", "/integrations/orient_eld/companies/ACME/credentials",
     "remove_company_credential_generic"),
    ("POST", "/integrations/orient_eld/companies/ACME/actions/test",
     "test_company_connection_generic"),
])
def test_a_second_per_company_provider_is_served(method, path, expected):
    assert _winner(method, path) == expected


def test_the_shared_account_level_routes_still_win_their_own_paths():
    """The generic family must not have eaten the connect/feeds surface
    on its way past."""
    assert _winner("POST", "/integrations/orient_eld/connect") == \
        "connect_integration"
    assert _winner("GET", "/integrations/orient_eld/feeds") == \
        "provider_feeds"
    assert _winner("GET", "/integrations/orient_eld/cadences") == \
        "effective_cadences"


# ── Every per-company provider in the catalog has a route ─────────

def test_every_per_company_provider_can_reach_a_companies_route():
    """``auth_kind="api_token"`` is what makes the dashboard render the
    per-company key matrix.  A provider that renders it and 404s on it
    is worse than one that does not render it at all."""
    from adapters.telematics.catalog import PROVIDER_CATALOG, ProviderStatus

    for pid, entry in PROVIDER_CATALOG.items():
        if entry.status != ProviderStatus.AVAILABLE:
            continue
        if entry.auth_kind != "api_token":
            continue
        assert _winner("GET", f"/integrations/{pid}/companies"), (
            f"{pid} renders the per-company key matrix but no route "
            "answers it"
        )


# ── The gate ──────────────────────────────────────────────────────

def test_every_generic_company_route_is_permission_gated():
    """Per-company API keys are integration credentials; even knowing
    WHICH companies have one is more than a member should see."""
    import capabilities.integrations.shared.companies_router as mod

    routes = [r for r in mod.router.routes if hasattr(r, "methods")]
    assert len(routes) == 4, f"expected 4 generic routes, found {len(routes)}"

    for r in routes:
        gate_calls = [
            d.call for d in r.dependant.dependencies if d.call is not None
        ]
        assert mod._owner_only in gate_calls, (
            f"{','.join(sorted(r.methods))} {r.path} does not depend on the "
            "can_manage_integrations gate"
        )


def test_the_gate_is_the_integration_permission_and_not_a_weaker_one():
    """Pinned by name: a later refactor that swaps this for
    ``can_view_integrations`` would still pass the test above."""
    import inspect

    import capabilities.integrations.shared.companies_router as mod

    src = inspect.getsource(mod)
    assert '_owner_only = require_permission("can_manage_integrations")' in src
