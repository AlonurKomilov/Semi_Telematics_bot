"""The API does not hand out a map of itself.

FastAPI publishes an interactive docs page, a ReDoc page, and the raw
OpenAPI schema. All three were on: the schema is 1.75 MB describing every
route, parameter and model we expose — including roughly twenty-five
admin write endpoints — and it answered unauthenticated on dash., api.
and app.

A probe against production on 2026-09-08 fetched it six times and then
walked exactly that admin surface. Every one of those calls was refused,
which is the system working; drawing the attacker the map first is not.

Nothing reads the schema at runtime — no client generator, no browser
extension, no test — so it is off unless ``API_DOCS=1``.
"""

from __future__ import annotations

import importlib
import os

import pytest


DOC_PREFIXES = ("/api/docs", "/api/redoc", "/api/openapi", "/docs", "/redoc", "/openapi")


def _doc_routes(app) -> set[str]:
    return {
        r.path for r in app.routes
        if getattr(r, "path", "").startswith(DOC_PREFIXES)
        # the oauth2 redirect helper is inert without the docs page
        and not r.path.endswith("/oauth2-redirect")
    }


@pytest.fixture
def api_module(monkeypatch):
    import interfaces.api.app as app_module
    return app_module


@pytest.mark.parametrize("value", [None, "0", "", "false", "no", " "])
def test_the_schema_is_absent_unless_explicitly_enabled(api_module, monkeypatch, value):
    """Anything other than exactly "1" leaves the docs off.

    A truthy-looking string must not open them by accident — this is the
    kind of flag that gets set to "false" and quietly means True.
    """
    if value is None:
        monkeypatch.delenv("API_DOCS", raising=False)
    else:
        monkeypatch.setenv("API_DOCS", value)

    assert _doc_routes(api_module.create_api()) == set()


def test_all_three_renderers_come_back_together(api_module, monkeypatch):
    """docs, redoc and the schema — ReDoc was never set here, so it sat
    on the framework default and shipped open with the others."""
    monkeypatch.setenv("API_DOCS", "1")

    assert _doc_routes(api_module.create_api()) == {
        "/api/docs", "/api/redoc", "/api/openapi.json"}


def test_a_hidden_schema_is_a_404_not_a_403(api_module, monkeypatch):
    """``None`` makes FastAPI omit the route entirely.

    A 403 would confirm the endpoint exists and is merely withheld; a
    404 is indistinguishable from every other unknown path.
    """
    monkeypatch.delenv("API_DOCS", raising=False)
    app = api_module.create_api()

    from fastapi.testclient import TestClient

    # No ``with``: the context manager runs the lifespan, which wants a
    # database.  The routing table is what is under test here.
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/api/openapi.json", "/api/docs", "/api/redoc"):
        assert client.get(path).status_code == 404, path


def test_the_gate_reads_the_environment_at_build_time(api_module, monkeypatch):
    """Not import time — otherwise flipping the env needs a code reload
    rather than the API restart the runbook tells an operator to do."""
    monkeypatch.delenv("API_DOCS", raising=False)
    assert _doc_routes(api_module.create_api()) == set()

    monkeypatch.setenv("API_DOCS", "1")
    assert _doc_routes(api_module.create_api())  # same module object, no reload
