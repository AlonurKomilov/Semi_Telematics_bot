"""A half-connected company is a 400, not a 500 — driven as a REQUEST.

The reported failure: ``GET /api/reports/export`` returned 500 with
``ValueError: Unknown company: TestCo``.  The message named the wrong
cause.  ``build_multi_company_client`` builds its company -> client map
by SKIPPING every company whose API key is unset — its own docstring
says "silently skipped" — so the ordinary way to reach that raise is a
company that genuinely exists, is active, and appears in the dashboard's
own company filter, but has no key yet.  An operator part-way through
onboarding got a stack trace.

~44 endpoints take a ``company`` parameter and hand it to that client,
so the fix is one app-wide handler rather than 44 guards.  These tests
drive the ASGI app because the handler REGISTRATION is the part that can
silently not work — a unit test of the exception class cannot see
whether FastAPI actually routes it.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from adapters.telematics.errors import NoTelematicsClientError

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    from interfaces.api.app import create_api
    app = create_api()

    # A probe route that fails exactly the way the real ones do.  Using
    # a probe rather than /reports/export keeps the test about the
    # HANDLER: no account, no companies, no Samsara credentials to
    # arrange, and it stays true if the reports router moves.
    probe = APIRouter()

    @probe.get("/_probe/unconnected")
    async def _probe():
        raise NoTelematicsClientError("TestCo", ["ACME", "BETA"])

    app.include_router(probe, prefix="/api")
    return app


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        return await c.get(path)


async def test_an_unconnected_company_answers_400_not_500(api):
    r = await _get(api, "/api/_probe/unconnected")
    assert r.status_code == 400, (
        f"expected a client error, got {r.status_code}: {r.text}")


async def test_the_message_names_both_causes_and_the_fix(api):
    r = await _get(api, "/api/_probe/unconnected")
    detail = r.json()["detail"]
    assert "TestCo" in detail
    # The old message asserted one cause and was wrong in the common
    # case.  This one must not repeat that.
    assert "not a company on this account" in detail
    assert "API key" in detail
    assert "Integration card" in detail, "the message must say where to fix it"


async def test_the_other_companies_codes_do_not_leak_into_the_response(api):
    """A caller restricted to one company must not learn the others.

    The connected codes are on the exception for the LOG line; putting
    them in an HTTP body would turn an error message into a directory.
    """
    r = await _get(api, "/api/_probe/unconnected")
    body = r.text
    assert "ACME" not in body
    assert "BETA" not in body


async def test_the_error_is_still_a_ValueError():
    """Existing ``except ValueError`` callers must keep working.

    The bot's scheduled-report generator catches ValueError to turn an
    unknown report type into a soft failure; narrowing this error must
    not slip past a handler that already exists.
    """
    assert issubclass(NoTelematicsClientError, ValueError)
    with pytest.raises(ValueError):
        raise NoTelematicsClientError("TestCo")


async def test_the_adapter_raises_the_typed_error_for_a_company_it_cannot_reach():
    """The raise site itself — the thing the 500 came from."""
    from adapters.telematics.samsara.client import MultiCompanyClient

    client = MultiCompanyClient({}, account_id=1)

    async def _never_called(_c):  # pragma: no cover - must not run
        raise AssertionError("coro_fn ran for an unreachable company")

    with pytest.raises(NoTelematicsClientError) as got:
        await client._run_per_company(_never_called, company="TestCo")
    assert got.value.company == "TestCo"


# ── the export endpoint resolves the code before dispatching ──────
#
# Driven by calling the endpoint function with a stub client rather
# than over HTTP: the 400 mapping is already covered above, and what
# needs proving here is that BOTH formats reach the same decision
# before either builder runs.

def _reporting_router(monkeypatch, codes):
    from capabilities.reporting import router as rr

    class _StubClient:
        company_codes = list(codes)

    async def _get_client(_account_id, **_kw):
        return _StubClient()

    async def _no_restriction(_user):
        return []

    async def _holds(_user, _perm):
        return True

    monkeypatch.setattr(rr, "get_client", _get_client, raising=True)
    monkeypatch.setattr(rr, "get_user_company_codes", _no_restriction,
                        raising=True)
    monkeypatch.setattr(rr, "holds", _holds, raising=True)
    return rr


_USER = {"account_id": 10000001, "role": "owner", "user_id": 1}


@pytest.mark.parametrize("fmt", ["pdf", "csv"])
@pytest.mark.parametrize("report_type", ["faults", "fuel", "health",
                                         "efficiency"])
async def test_export_rejects_an_unreachable_company_in_both_formats(
    fmt, report_type, monkeypatch,
):
    """One answer per request, whichever report and whichever format.

    Before this, an unreachable company was an error from the reports
    that pass ``company`` downstream and a silent zero-row file from
    the ones that fetch account-wide and filter locally — the same
    request answered two ways depending on which report you asked for.
    """
    rr = _reporting_router(monkeypatch, ["ACME", "BETA"])

    with pytest.raises(NoTelematicsClientError) as got:
        await rr.export_report(report_type=report_type, fmt=fmt,
                               company="TestCo", days=7, user=_USER)
    assert got.value.company == "TestCo"


@pytest.mark.parametrize("fmt", ["pdf", "csv"])
async def test_a_differently_cased_company_resolves_to_its_canonical_code(
    fmt, monkeypatch,
):
    """The client map is keyed by exact code; every filter uppercases.

    So a correctly-spelled but differently-cased code passed the
    company filters and then resolved no client — reaching the raise as
    if it named nothing at all.  The canonical spelling is what goes
    downstream now.
    """
    rr = _reporting_router(monkeypatch, ["ACME"])
    seen: dict = {}

    import capabilities.reporting.data_fetch as df

    async def _pdf(_acct, _rt, *, company=None, **_kw):
        seen["company"] = company
        return __import__("io").BytesIO(b"x"), "cap", "Stem"

    async def _csv(_acct, _rt, *, company=None, **_kw):
        seen["company"] = company
        return __import__("io").BytesIO(b"x"), "Stem"

    monkeypatch.setattr(df, "build_report_pdf", _pdf, raising=True)
    monkeypatch.setattr(df, "build_report_csv", _csv, raising=True)

    await rr.export_report(report_type="faults", fmt=fmt, company="acme",
                           days=7, user=_USER)

    assert seen["company"] == "ACME", (
        "the caller's casing must not reach the client map")
