"""Five companies, five API keys, one account — the real setup.

The owner holds one ORIENT ELD key PER COMPANY, which is how ORIENT
issues them. The platform schedules per ACCOUNT. Everything here is
about the gap between those two facts, and it is written before the
keys are entered rather than after, because two of these failures are
silent.

What a reader should take from this file:

  * the credentials the DASHBOARD actually produces are the credentials
    the builder actually consumes — connect writes one shape, the
    per-company card writes another, and both must work;
  * once per-company keys exist the account-level one is IGNORED, never
    lent to a company that has none;
  * one dead key costs that company's drivers and nobody else's;
  * two companies cannot silently overwrite each other's drivers.
"""

from __future__ import annotations

import pytest

from adapters.telematics.orient_eld.client import (
    MultiCompanyOrientClient,
    build_multi_company_orient_client,
)
from adapters.telematics.orient_eld.provider import OrientEldProvider


FIVE = ["PREMIER", "APEX", "BORDER", "CASCADE", "DELTA"]


def _row(driver_id: int, company: str, **kw):
    base = {
        "driver_id": driver_id,
        "name": "Driver",
        "surname": str(driver_id),
        "status": "DRIVING",
        "vehicle_number": "001",
        "datetime_utc": "2026-09-15T05:18:18Z",
        "status_activation_time_utc": "2026-09-15T05:19:42",
        "location_datetime_utc": "2026-09-15T05:18:18Z",
        "_company_code": company,
    }
    base.update(kw)
    return base


class _Company:
    """One company's ORIENT endpoint, as the fan-out sees it."""

    def __init__(self, rows, boom: str = ""):
        self._rows = rows
        self._boom = boom
        self.asked = 0

    async def get_tracking(self):
        self.asked += 1
        if self._boom:
            raise RuntimeError(self._boom)
        return [dict(r) for r in self._rows]

    async def close(self):
        return None


# ── The credentials the dashboard actually writes ─────────────────

def test_the_connect_form_writes_a_shape_the_builder_reads():
    """The shared connect route has ONE token field and posts
    ``{"api_token": …}``. A builder that only understood a companies
    map would make ORIENT impossible to connect from the dashboard at
    all — the card would render and the button would fail."""
    client = build_multi_company_orient_client({"api_token": "k"})
    assert client.company_codes == ["default"]


def test_the_per_company_card_writes_a_shape_the_builder_reads():
    """PUT /integrations/{provider}/companies/{code}/credentials stores
    into ``credentials["companies"][code]`` — five calls, five keys."""
    creds = {"companies": {c: f"key-{c}" for c in FIVE}}
    client = build_multi_company_orient_client(creds)
    assert client.company_codes == sorted(FIVE)
    assert len(client) == 5


def test_a_company_key_is_never_lent_to_another_company():
    """The divergence from Samsara's builder, and the reason for it.

    Samsara falls back to one account-level token for any company
    without its own. Doing that here would not FAIL — it would succeed
    and return the first company's drivers under the other's name,
    which is the worst way to be wrong about whose hours you are
    reading.
    """
    creds = {"companies": {"PREMIER": "k1"}, "api_token": "account-wide"}
    client = build_multi_company_orient_client(creds)
    assert client.company_codes == ["PREMIER"], (
        "a company with no key of its own borrowed one"
    )


def test_the_account_level_key_only_applies_while_no_company_has_one():
    """Before the five keys are entered, the single connect key is what
    runs the integration. After even one per-company key exists, it
    stops applying — otherwise entering key 1 of 5 would leave four
    companies quietly reading company 1's drivers."""
    assert build_multi_company_orient_client(
        {"api_token": "k"}).company_codes == ["default"]
    assert build_multi_company_orient_client(
        {"api_token": "k", "companies": {"PREMIER": "k1"}},
    ).company_codes == ["PREMIER"]


def test_a_company_added_without_a_key_is_absent_not_broken():
    creds = {"companies": {c: f"key-{c}" for c in FIVE} | {"NEWCO": ""}}
    assert "NEWCO" not in build_multi_company_orient_client(creds).company_codes


# ── Five companies, asked together ────────────────────────────────

@pytest.mark.asyncio
async def test_all_five_are_asked_and_their_drivers_merge():
    clients = {
        code: _Company([_row(1000 + i * 10 + n, code) for n in range(4)])
        for i, code in enumerate(FIVE)
    }
    provider = OrientEldProvider(MultiCompanyOrientClient(clients))

    snaps = await provider.get_driver_hos()

    assert all(c.asked == 1 for c in clients.values()), "a company was skipped"
    assert len(snaps) == 20
    assert len({s.provider_driver_id for s in snaps}) == 20


@pytest.mark.asyncio
async def test_one_dead_key_costs_only_that_companys_drivers():
    """The failure this account will actually meet — a key revoked or
    rotated on ORIENT's side. Four companies must keep reporting; a
    single raise for the whole account would make the entire fleet look
    off duty."""
    clients = {c: _Company([_row(100 + i, c)])
               for i, c in enumerate(FIVE)}
    clients["BORDER"] = _Company([], boom="401 api key rejected")
    provider = OrientEldProvider(MultiCompanyOrientClient(clients))

    snaps = await provider.get_driver_hos()

    assert len(snaps) == 4, "one dead key emptied more than its own company"


@pytest.mark.asyncio
async def test_every_key_failing_is_an_empty_answer_not_an_exception():
    """The ingest reads an empty list as "nothing to write" and leaves
    the last readings in place to age into staleness. It must not have
    to catch an exception to get there."""
    clients = {c: _Company([], boom="down") for c in FIVE}
    provider = OrientEldProvider(MultiCompanyOrientClient(clients))
    assert await provider.get_driver_hos() == []


# ── The identity risk that only appears at five ───────────────────

@pytest.mark.asyncio
async def test_two_companies_cannot_silently_overwrite_one_driver():
    """driver_hos_live is keyed (account_id, provider_id,
    provider_driver_id) — no company in it. With one key that cannot
    bite. With five, the same driver_id from two companies would have
    one duty status erase the other, with nothing logged and nothing
    visibly wrong.

    ORIENT's API says ids are unique across companies (driver_id is a
    top-level filter accepted alongside dot_number/mc_number). This
    guards the case where that turns out to be false.
    """
    provider = OrientEldProvider(MultiCompanyOrientClient({
        "PREMIER": _Company([_row(19192, "PREMIER")]),
        "APEX": _Company([_row(19192, "APEX")]),
    }))

    snaps = await provider.get_driver_hos()

    assert len(snaps) == 2, "one company's driver overwrote the other's"
    assert len({s.provider_driver_id for s in snaps}) == 2, (
        "both survived the fetch but would collide in the store"
    )


@pytest.mark.asyncio
async def test_the_collision_is_reported_loudly(caplog):
    """A tripwire nobody reads is not a tripwire. If this fires, the
    'ids are globally unique' assumption is wrong and the fix is a
    composite key — that decision needs the evidence in the log."""
    import logging

    provider = OrientEldProvider(MultiCompanyOrientClient({
        "PREMIER": _Company([_row(19192, "PREMIER")]),
        "APEX": _Company([_row(19192, "APEX")]),
    }))
    with caplog.at_level(logging.ERROR):
        await provider.get_driver_hos()

    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "19192" in msg
    assert "PREMIER" in msg and "APEX" in msg


@pytest.mark.asyncio
async def test_one_company_repeating_a_driver_is_one_driver():
    """Distinct from the collision above: a page that lists the same
    driver twice is one person, and inventing a second row for them
    would report a driver who does not exist."""
    provider = OrientEldProvider(MultiCompanyOrientClient({
        "PREMIER": _Company([_row(19192, "PREMIER"), _row(19192, "PREMIER")]),
    }))
    snaps = await provider.get_driver_hos()
    assert len(snaps) == 1
    assert snaps[0].provider_driver_id == "19192"


@pytest.mark.asyncio
async def test_the_common_case_keeps_the_vendors_own_id_untouched():
    """No prefixing, no decoration. The raw ORIENT id is what a future
    driver-link table will match on, and mangling it on every account
    to guard a case that has never happened would cost more than it
    saves."""
    provider = OrientEldProvider(MultiCompanyOrientClient({
        c: _Company([_row(1000 + i, c)]) for i, c in enumerate(FIVE)
    }))
    snaps = await provider.get_driver_hos()
    assert all(s.provider_driver_id.isdigit() for s in snaps)
