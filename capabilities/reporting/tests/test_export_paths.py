"""Every report the registry advertises must export, from the right source.

Two failures shipped here, and they are the same failure twice.

The one a user reported: the registry declared
``data_method="get_fault_codes"`` and the router resolved it with
``getattr(client, ...)``.  The client's method had been renamed to
``get_vehicles_with_faults`` — a string names nothing the interpreter
checks, so nothing failed at import, at startup, or in any test.  It
failed in production, on the Faults CSV, with ``AttributeError``.  Fuel
was broken identically and nobody had noticed at all.

The one nobody reported: Health and Efficiency read
``get_vehicles_overview`` instead of ``get_vehicle_health`` /
``get_fleet_efficiency``.  The overview carries none of the fields
those reports print, so Health rendered every truck with zero alerts
and Efficiency crashed on a ``KeyError``.  A wrong ANSWER is worse than
an exception, because only the exception gets reported.

So the stubs below give every service a differently-named truck, and
each report must contain its own service's truck and nobody else's.
Reading the wrong upstream is then a red test rather than a plausible
looking file.
"""

from __future__ import annotations

import io

import pytest

from capabilities.reporting import data_fetch
from capabilities.reporting.registry import REPORTS_BY_KEY, keys_with_api_export


EXPORTABLE = keys_with_api_export()

# One service, one truck name.  ``_org`` and the per-report payload
# fields are what each generator prints; the NAME is what identifies
# which service the report actually read.
_OVERVIEW = [{"name": "TRUCK-OVERVIEW", "_org": "ACME", "id": 1,
              "fuel": {"percent": 40}, "def_level": {"percent": 50},
              "location": {}},
             {"name": "TRUCK-OVERVIEW-B", "_org": "ACME", "id": 2,
              "fuel": {"percent": 90}, "def_level": {"percent": 80},
              "location": {}}]
_FAULTED = [{"name": "TRUCK-FAULTS", "_org": "ACME", "id": 3,
             "_dtcs": [{"code": "SPN-100"}]},
            {"name": "TRUCK-FAULTS-B", "_org": "ACME", "id": 4,
             "_dtcs": [{"code": "SPN-200"}]}]
_HEALTH = [{"name": "TRUCK-HEALTH", "_org": "ACME", "id": 5,
            "_health": {"battery_v": 12.4}, "_health_alerts": ["low_battery"]},
           {"name": "TRUCK-HEALTH-B", "_org": "ACME", "id": 6,
            "_health": {"battery_v": 13.1}, "_health_alerts": []}]
_EFFICIENCY = [{"name": "TRUCK-EFFICIENCY", "_org": "ACME", "id": 7,
                "_engine_hours": 10.0, "_engine_s": 36000, "_miles": 500.0,
                "_idle_hours": 2.0, "_driving_hours": 8.0, "_fuel_gal": 80.0,
                "_mpg": 6.25, "_driver_name": "A. Klein"},
               {"name": "TRUCK-EFFICIENCY-B", "_org": "ACME", "id": 8,
                "_engine_hours": 5.0, "_engine_s": 18000, "_miles": 250.0,
                "_idle_hours": 1.0, "_driving_hours": 4.0, "_fuel_gal": 40.0,
                "_mpg": 6.25, "_driver_name": "B. Ruiz"}]

# Which service each report is REQUIRED to read.
OWN_TRUCK = {
    "faults": "TRUCK-FAULTS",
    "fuel": "TRUCK-OVERVIEW",
    "health": "TRUCK-HEALTH",
    "efficiency": "TRUCK-EFFICIENCY",
}
ALL_TRUCKS = set(OWN_TRUCK.values())


@pytest.fixture
def services(monkeypatch):
    """Stub all four upstreams, each with its own identifiable trucks."""
    monkeypatch.setattr(data_fetch, "_prepare_companies", _async_noop,
                        raising=True)
    monkeypatch.setattr(data_fetch, "_svc_vehicles_overview",
                        _returning(_OVERVIEW), raising=True)
    monkeypatch.setattr(data_fetch, "_svc_vehicles_with_faults",
                        _returning((_FAULTED, len(_OVERVIEW), {})),
                        raising=True)
    monkeypatch.setattr(data_fetch, "_svc_vehicle_health",
                        _returning(_HEALTH), raising=True)
    monkeypatch.setattr(data_fetch, "_svc_fleet_efficiency",
                        _returning(_EFFICIENCY), raising=True)


# ── the registry's promise ────────────────────────────────────────

def test_the_registry_advertises_the_four_exports():
    # Guards the list itself: camera has a PDF but no export path, and a
    # new report added to the endpoint's Query description without a
    # builder is the failure the parametrisation below would hide.
    assert EXPORTABLE == ["faults", "fuel", "health", "efficiency"]
    assert REPORTS_BY_KEY["camera"].api_export is False


@pytest.mark.parametrize("key", EXPORTABLE)
def test_every_advertised_export_has_both_builders(key):
    assert key in data_fetch._CSV_DISPATCH
    assert key in data_fetch._DISPATCH


# ── the right upstream ────────────────────────────────────────────

@pytest.mark.parametrize("key", EXPORTABLE)
@pytest.mark.asyncio
async def test_every_csv_export_reads_its_own_service(key, services):
    """The guard the two shipped bugs both needed.

    Each service hands back a uniquely named truck, so a builder
    reaching for the wrong one prints somebody else's fleet.
    """
    buf, stem = await data_fetch.build_report_csv(10000001, key)

    assert isinstance(buf, io.BytesIO)
    assert stem and not stem.endswith(".csv")
    body = buf.getvalue().decode("utf-8", "replace")

    mine = OWN_TRUCK[key]
    assert mine in body, f"{key} CSV did not read {mine}'s service"
    for other in ALL_TRUCKS - {mine}:
        assert other not in body, f"{key} CSV read {other}'s service instead"


@pytest.mark.parametrize("key", EXPORTABLE)
@pytest.mark.asyncio
async def test_every_pdf_export_reads_its_own_service(key, services,
                                                     monkeypatch):
    """Same check for the PDF half — where Health and Efficiency broke.

    The generators are stubbed to capture their input rather than
    render: what is under test is which rows arrive, not the layout.
    """
    captured: dict = {}
    _stub_pdf_generators(monkeypatch, captured)

    buf, _caption, stem = await data_fetch.build_report_pdf(10000001, key)

    assert buf is not None and stem
    assert key in captured, f"{key} PDF never reached its generator"
    rows, _kwargs = captured[key]
    names = {r["name"] for r in rows}
    assert OWN_TRUCK[key] in names, f"{key} PDF did not read its own service"
    for other in ALL_TRUCKS - {OWN_TRUCK[key]}:
        assert other not in names, f"{key} PDF read {other}'s service instead"


@pytest.mark.asyncio
async def test_an_unknown_csv_type_raises_rather_than_returning_empty(services):
    with pytest.raises(ValueError):
        await data_fetch.build_report_csv(10000001, "camera")


# ── the caller's scope reaches every list, in both formats ────────

@pytest.mark.parametrize("key", EXPORTABLE)
@pytest.mark.asyncio
async def test_the_callers_scope_narrows_every_csv_export(key, services):
    """A driver's assigned-truck filter must survive the consolidation.

    The old CSV branch applied ``filter_by_allowed_companies`` and
    ``filter_by_assigned_trucks`` in the router, on the one list it
    held.  Moving the fetch into ``data_fetch`` would have dropped both
    — a widening, and the kind that shows up as a driver downloading
    the whole account.
    """
    keep = OWN_TRUCK[key]
    consulted: list[list[dict]] = []

    async def scope(rows):
        consulted.append(rows)
        return [r for r in rows if r["name"] == keep]

    buf, _stem = await data_fetch.build_report_csv(10000001, key, scope=scope)

    assert consulted, f"{key} never consulted the caller's scope"
    body = buf.getvalue().decode("utf-8", "replace")
    assert keep in body, f"{key} dropped a row the scope allowed"
    assert f"{keep}-B" not in body, f"{key} exported a row the scope excluded"


@pytest.mark.parametrize("key", EXPORTABLE)
@pytest.mark.asyncio
async def test_the_callers_scope_narrows_every_pdf_export(key, services,
                                                          monkeypatch):
    """The gap the CSV fix left open one branch away.

    ``/reports/export?fmt=pdf`` applied neither filter, so a driver
    restricted to two trucks could download a PDF of the whole account
    — while the same request in CSV was correctly narrowed.
    """
    captured: dict = {}

    _stub_pdf_generators(monkeypatch, captured)
    keep = OWN_TRUCK[key]

    async def scope(rows):
        return [r for r in rows if r["name"] == keep]

    await data_fetch.build_report_pdf(10000001, key, scope=scope)

    rows, kwargs = captured[key]
    names = {r["name"] for r in rows}
    assert names == {keep}, f"{key} PDF ignored the caller's scope: {names}"

    # The faults PDF also renders a SECOND list — the fleet overview it
    # draws as background context.  A scope is not a view preference,
    # so background context is no exception: it is rows this caller may
    # not see, printed on a page they are handed.
    for kw_name, extra in kwargs.items():
        if not isinstance(extra, list) or not extra:
            continue
        if not all(isinstance(r, dict) and "name" in r for r in extra):
            continue
        assert {r["name"] for r in extra} <= {keep}, (
            f"{key} PDF passed unscoped rows as {kw_name}")


@pytest.mark.asyncio
async def test_faults_counts_the_denominator_through_the_same_scope(
    services, monkeypatch,
):
    """A scoped caller may not see every truck COUNTED either.

    ``generate_fault_csv`` takes a total alongside the faulted list.
    That total comes from the account-wide service, so handing it
    through unscoped would print "2 of 188" to someone who may see one
    truck.
    """
    captured: dict = {}

    def fake_csv(faulted, total, company_filter=None):
        captured["total"] = total
        captured["faulted"] = list(faulted)
        return io.BytesIO(b"stub")

    async def scope(rows):
        return rows[:1]

    monkeypatch.setattr(data_fetch, "_svc_vehicles_with_faults",
                        _returning((_FAULTED, 188, {})), raising=True)
    monkeypatch.setattr(data_fetch, "generate_fault_csv", fake_csv,
                        raising=True)

    await data_fetch.build_report_csv(10000001, "faults", scope=scope)

    assert captured["total"] == 1
    assert len(captured["faulted"]) == 1


# ── helpers ───────────────────────────────────────────────────────

def _stub_pdf_generators(monkeypatch, captured: dict) -> None:
    """Capture what each PDF generator is HANDED, positional and keyword.

    The layout is not under test; which rows arrive is.  Keywords are
    kept because the fault report renders a second vehicle list through
    one (``all_vehicles=``).
    """
    def _capture(name):
        def _gen(rows, *a, **kw):
            captured[name] = (list(rows), kw)
            return io.BytesIO(b"stub-pdf")
        return _gen

    for attr, name in (
        ("generate_fault_report_pdf", "faults"),
        ("generate_fuel_report_pdf", "fuel"),
        ("generate_vehicle_health_pdf", "health"),
        ("generate_fleet_efficiency_pdf", "efficiency"),
    ):
        monkeypatch.setattr(data_fetch, attr, _capture(name), raising=True)


async def _async_noop(*_a, **_kw):
    return None


def _returning(value):
    async def _f(*_a, **_kw):
        return value
    return _f
