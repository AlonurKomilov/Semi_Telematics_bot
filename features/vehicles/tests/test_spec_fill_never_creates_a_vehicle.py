"""A second opinion about a truck is not a vote that the truck exists.

`capabilities/source` arbitrates several sources describing the SAME
record, field by field, with `manual` above all of them and the account
owner choosing the order. ORIENT ELD joining that list is what keeps a
VIN and a plate arriving when the other integration goes dark — the
feature reads the MERGED vehicle, never "Samsara's vehicle".

The danger sits one step away, and it is money.

The registry is what billing counts (`BILLABLE_VEHICLE_TYPES`, the daily
`billing_quantity_sync`). `recon.may_add` fails OPEN — its own docstring
says "unknown sources may" — which is right for a source that has always
registered vehicles and wrong for one being added to a live account with
trucks already on the invoice. A brand-new source that creates even a
handful of rows moves a customer's bill, silently, on its first tick.

So this feed does not RELY on policy. It passes `may_create=False`, and
that argument outranks the policy on purpose: no owner can turn a second
opinion into a creator from a panel about field precedence.
"""

from __future__ import annotations

import pytest

import features.vehicles.spec_ingest as spec_ingest


class _Provider:
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom
        self.asked = 0

    async def get_vehicle_spec(self):
        self.asked += 1
        if self._boom:
            raise RuntimeError("upstream 503")
        return list(self._rows)


class _Tenant:
    """Records exactly what the registry was asked to do."""

    def __init__(self):
        self.calls: list[dict] = []

    async def project_external_vehicles(self, account_id, rows, *,
                                        vehicle_type, source,
                                        may_create=None):
        self.calls.append({
            "source": source, "rows": rows,
            "vehicle_type": vehicle_type, "may_create": may_create,
        })
        return len(rows)


@pytest.fixture
def wired(monkeypatch):
    def _install(providers: dict):
        tenant = _Tenant()

        async def _resolve(account_id, capability):
            return list(providers)

        async def _tenant(account_id):
            return tenant

        async def _client(account_id, provider_id, **kw):
            return providers[provider_id]

        monkeypatch.setattr(spec_ingest, "resolve_all_providers_for", _resolve)
        monkeypatch.setattr(spec_ingest, "get_tenant_db", _tenant)
        monkeypatch.setattr(spec_ingest, "get_telematics_client", _client)
        return tenant
    return _install


def _row(unit="6729", **kw):
    base = {"unit_number": unit, "vin": "1FUJGLDR8CSBP1234",
            "plate_number": "AB-1234", "make": "FREIGHTLINER",
            "model": "TT", "company_code": "PTG"}
    base.update(kw)
    return base


# ── The money guard ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_feed_can_never_create_a_vehicle():
    """Stated as the thing the caller promises, not as a setting."""
    tenant = None

    class _T(_Tenant):
        pass

    t = _T()

    async def _resolve(account_id, capability):
        return ["orient_eld"]

    async def _tenant(account_id):
        return t

    async def _client(account_id, provider_id, **kw):
        return _Provider([_row()])

    import features.vehicles.spec_ingest as m
    m.resolve_all_providers_for = _resolve
    m.get_tenant_db = _tenant
    m.get_telematics_client = _client

    await m.ingest_vehicle_spec(7)

    assert t.calls, "nothing reached the registry"
    for call in t.calls:
        assert call["may_create"] is False, (
            "this feed asked the registry for permission to create "
            "vehicles — the registry says yes to unknown sources, and "
            "the registry is what billing counts"
        )


@pytest.mark.asyncio
async def test_the_promise_is_in_the_source_not_the_policy():
    """Grep-proof: the argument must be literally present, because a
    default that happens to be safe today is not a promise."""
    import ast
    import inspect

    src = inspect.getsource(spec_ingest)
    assert "may_create=False" in src

    # A CALL, not the word. The module explains in prose why it does
    # not consult the policy, and grepping the string would flag its own
    # reasoning — the first version of this test did exactly that.
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "may_add" not in called, (
        "this module consulted the lifecycle policy — it has no "
        "permission to ask for"
    )


# ── The merge itself ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_provider_offering_a_spec_is_asked(wired):
    """A union of opinions. Asking only the first in catalog order
    would throw away the second one, which is the entire reason this
    feed exists."""
    providers = {
        "samsara": _Provider([_row(unit="100")]),
        "orient_eld": _Provider([_row(unit="200")]),
    }
    tenant = wired(providers)

    merged = await spec_ingest.ingest_vehicle_spec(7)

    assert providers["samsara"].asked == 1
    assert providers["orient_eld"].asked == 1
    assert merged == 2
    assert {c["source"] for c in tenant.calls} == {"samsara", "orient_eld"}


@pytest.mark.asyncio
async def test_each_providers_rows_go_in_under_its_own_name(wired):
    """`source` is what the precedence panel ranks and what
    `field_provenance` records — mislabel it and the owner's choice
    applies to the wrong provider."""
    tenant = wired({"orient_eld": _Provider([_row()])})
    await spec_ingest.ingest_vehicle_spec(7)
    assert tenant.calls[0]["source"] == "orient_eld"


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_stop_the_other(wired):
    providers = {
        "samsara": _Provider([], boom=True),
        "orient_eld": _Provider([_row()]),
    }
    tenant = wired(providers)
    assert await spec_ingest.ingest_vehicle_spec(7) == 1
    assert [c["source"] for c in tenant.calls] == ["orient_eld"]


@pytest.mark.asyncio
async def test_an_empty_answer_writes_nothing(wired):
    """Both sources agreeing is the healthy state, not a stalled feed."""
    tenant = wired({"orient_eld": _Provider([])})
    assert await spec_ingest.ingest_vehicle_spec(7) == 0
    assert tenant.calls == []


@pytest.mark.asyncio
async def test_nothing_offering_a_spec_is_zero_not_a_raise(wired):
    tenant = wired({})
    assert await spec_ingest.ingest_vehicle_spec(7) == 0
    assert tenant.calls == []
