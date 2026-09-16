"""Linking a member to the driver an integration reports.

The roster and the device do not share an identity. An ELD knows
"driver 19192"; we know "Jean Kaneza, user 84". Somebody says once that
those are the same person, and every feed, scorecard and pay run
follows the link afterwards.

It is a HUMAN decision because there is nothing to match on. The
matcher Datatruck's import uses goes ref → CDL → email and refuses
NAME, since a wrong match writes one driver's licence number onto
another's record. Measured on the account this was built for, it would
link zero of sixty-nine: the ELD carries a CDL for all of them and our
roster carries none, and the two email sets do not intersect.

What the endpoints must never do is the thing that would be convenient:
move a link that already belongs to somebody else. That would put one
person's duty clocks, pay and scorecard onto another member, quietly.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import features.drivers.provider_links as pl


class _Tenant:
    def __init__(self, rows=(), boom=False):
        self._rows = list(rows)
        self.links: list[tuple] = []
        self._boom = boom

    async def get_driver_hos_live(self, account_id, user_id=None):
        if self._boom:
            raise RuntimeError("feed table missing")
        return list(self._rows)

    async def link_provider_driver(self, account_id, provider_id, user_id,
                                   ref, *, method="manual", linked_by=None):
        if ref == "taken":
            raise ValueError("that driver is already linked to another member")
        self.links.append((provider_id, user_id, ref, method, linked_by))


def _row(pid="orient_eld", ref="19192", name="Jean Kaneza",
         user_id=None, company="CFT", vehicle="6729"):
    return {
        "provider_id": pid, "provider_driver_id": ref,
        "driver_name": name, "user_id": user_id,
        "company_code": company, "provider_vehicle": vehicle,
    }


# ── The picker ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_options_are_the_drivers_actually_reporting():
    """Read from our own duty feed, not a fresh call to the vendor.

    ORIENT's driver roster carries no role and no active flag, so it
    cannot tell a current driver from somebody who left. A row in the
    duty feed means a person logged onto a vehicle."""
    out = await pl.list_provider_links(
        user={"account_id": 1}, tenant_db=_Tenant([_row()]))

    assert [p["provider_id"] for p in out["providers"]] == ["orient_eld"]
    d = out["providers"][0]["drivers"][0]
    assert d["provider_driver_id"] == "19192"
    assert d["driver_name"] == "Jean Kaneza"
    assert d["linked_user_id"] is None


@pytest.mark.asyncio
async def test_the_vendors_name_is_shown_because_it_is_the_only_one():
    """For an unlinked person the provider's spelling is all we have,
    and picking them out of a list is the whole task."""
    out = await pl.list_provider_links(
        user={"account_id": 1},
        tenant_db=_Tenant([_row(name="Khusan Miragzamov")]))
    assert out["providers"][0]["drivers"][0]["driver_name"] == "Khusan Miragzamov"


@pytest.mark.asyncio
async def test_progress_is_counted_as_done_out_of_total():
    """Not "2 still unlinked".

    Linking a whole ELD is one drawer at a time, and a counter that only
    ever shows what is LEFT never acknowledges the work already done.
    The account also always starts above zero the moment one link
    exists, which is the shape endowed progress needs.
    """
    out = await pl.list_provider_links(
        user={"account_id": 1}, tenant_db=_Tenant([
            _row(ref="a", user_id=None),
            _row(ref="b", user_id=84),
            _row(ref="c", user_id=None),
        ]))
    assert out["providers"][0]["linked"] == 1
    assert out["providers"][0]["total"] == 3


@pytest.mark.asyncio
async def test_two_providers_are_two_sections():
    out = await pl.list_provider_links(
        user={"account_id": 1}, tenant_db=_Tenant([
            _row(pid="samsara", ref="s1"), _row(pid="orient_eld", ref="o1"),
        ]))
    assert [p["provider_id"] for p in out["providers"]] == [
        "orient_eld", "samsara"]
    assert out["providers"][0]["name"] == "ORIENT ELD"


@pytest.mark.asyncio
async def test_each_section_says_what_kind_of_thing_the_provider_is():
    """The drawer's hand-written sections read "Samsara (telematics)"
    and "Datatruck (TMS)", so a reader learns to expect the kind. A
    generated section printing the bare name breaks that pattern on
    exactly the provider they know least about."""
    out = await pl.list_provider_links(
        user={"account_id": 1}, tenant_db=_Tenant([
            _row(pid="orient_eld", ref="o1"),
            _row(pid="datatruck", ref="d1"),
        ]))
    kinds = {p["provider_id"]: p["kind"] for p in out["providers"]}
    assert kinds["orient_eld"] == "telematics"
    assert kinds["datatruck"] == "TMS", "an initialism is not a word"


@pytest.mark.asyncio
async def test_a_missing_feed_table_is_an_empty_picker_not_a_500():
    """Before the first ingest there is nothing to pick from, and that
    is a state, not a failure."""
    out = await pl.list_provider_links(
        user={"account_id": 1}, tenant_db=_Tenant(boom=True))
    assert out["providers"] == []


# ── The write ─────────────────────────────────────────────────────

class _User:
    def __init__(self, account_id=1, role="driver"):
        self.account_id, self.role = account_id, role


class _Platform:
    def __init__(self, user=None):
        self._user = user

    async def get_user(self, user_id):
        return self._user


@pytest.mark.asyncio
async def test_a_link_is_written_with_who_made_it(monkeypatch):
    monkeypatch.setattr(pl, "resolve_user_id", _aid(9))
    monkeypatch.setattr(pl, "record_simple", _noop())
    tenant = _Tenant()

    out = await pl.set_provider_link(
        84, "orient_eld", pl.ProviderLinkUpdate(provider_driver_id="19192"),
        user={"account_id": 1, "role": "owner"},
        platform_db=_Platform(_User()), tenant_db=tenant)

    assert out["ok"] is True
    assert tenant.links == [("orient_eld", 84, "19192", "manual", 9)]


@pytest.mark.asyncio
async def test_taking_somebody_elses_driver_is_a_409(monkeypatch):
    """The convenient thing would be to move it. That moves one
    person's duty clocks, pay and scorecard onto another member."""
    monkeypatch.setattr(pl, "resolve_user_id", _aid(9))
    monkeypatch.setattr(pl, "record_simple", _noop())

    with pytest.raises(HTTPException) as e:
        await pl.set_provider_link(
            84, "orient_eld", pl.ProviderLinkUpdate(provider_driver_id="taken"),
            user={"account_id": 1, "role": "owner"},
            platform_db=_Platform(_User()), tenant_db=_Tenant())
    assert e.value.status_code == 409
    assert "already linked" in e.value.detail


@pytest.mark.asyncio
async def test_a_blank_unlinks(monkeypatch):
    monkeypatch.setattr(pl, "resolve_user_id", _aid(9))
    monkeypatch.setattr(pl, "record_simple", _noop())
    tenant = _Tenant()

    await pl.set_provider_link(
        84, "orient_eld", pl.ProviderLinkUpdate(provider_driver_id=""),
        user={"account_id": 1, "role": "owner"},
        platform_db=_Platform(_User()), tenant_db=tenant)

    assert tenant.links == [("orient_eld", 84, "", "manual", 9)]


@pytest.mark.asyncio
async def test_a_provider_nobody_declared_is_refused():
    """The id is stored and later resolved against the registry; an
    unknown one would be a link to nothing that looks like a link."""
    with pytest.raises(HTTPException) as e:
        await pl.set_provider_link(
            84, "not_a_provider", pl.ProviderLinkUpdate(provider_driver_id="x"),
            user={"account_id": 1, "role": "owner"},
            platform_db=_Platform(_User()), tenant_db=_Tenant())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_member_of_another_account_is_not_found():
    with pytest.raises(HTTPException) as e:
        await pl.set_provider_link(
            84, "orient_eld", pl.ProviderLinkUpdate(provider_driver_id="x"),
            user={"account_id": 1, "role": "owner"},
            platform_db=_Platform(_User(account_id=999)), tenant_db=_Tenant())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_an_equal_or_higher_role_cannot_be_relinked():
    """The same rank wall the Samsara link carries: rewriting the
    identity of somebody at or above your level is an escalation."""
    with pytest.raises(HTTPException) as e:
        await pl.set_provider_link(
            84, "orient_eld", pl.ProviderLinkUpdate(provider_driver_id="x"),
            user={"account_id": 1, "role": "driver"},
            platform_db=_Platform(_User(role="owner")), tenant_db=_Tenant())
    assert e.value.status_code == 403


def _aid(n):
    async def _f(_user):
        return n
    return _f


def _noop():
    async def _f(*a, **k):
        return None
    return _f
