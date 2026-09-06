"""The live map's engine choice — free by default, Google when bought.

The platform keeps two renderers on purpose: OpenStreetMap costs
nothing and is what every account gets, Google costs per map load and
is what a US carrier already runs their day on.  Which one an account
has is a stored setting; whether they MAY have it is billing's
question, answered by writing that same setting.

These pin the two rules that must not drift: a missing platform key
means nobody gets Google however they are set, and an engine name we
do not recognise is not trusted.
"""
import pytest

from features.location import map_engine as me


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv(me.ENV_GOOGLE_KEY, raising=False)


def test_the_default_is_the_engine_that_costs_nothing():
    assert me.normalise(None) == me.OSM
    assert me.normalise("") == me.OSM
    assert me.resolve(None) == me.OSM


def test_an_engine_we_do_not_know_is_not_trusted():
    """A typo in a stored value must not decide what a map is."""
    assert me.normalise("gooogle") == me.OSM
    assert me.normalise("mapbox") == me.OSM
    assert me.normalise("  GOOGLE  ") == me.GOOGLE


def test_google_without_a_platform_key_falls_back_rather_than_breaking(monkeypatch):
    """A blank frame reads as a broken product; the free map does not.

    A missing key is OUR misconfiguration, never the customer's, so it
    costs them a familiar map and nothing else.
    """
    assert not me.google_available()
    assert me.resolve("google") == me.OSM


def test_google_with_a_key_is_what_the_account_asked_for(monkeypatch):
    monkeypatch.setenv(me.ENV_GOOGLE_KEY, "AIza-test")
    assert me.google_available()
    assert me.resolve("google") == me.GOOGLE


class _Tenant:
    def __init__(self, value=None, boom=False):
        self.value, self.boom = value, boom

    async def get_account_setting(self, account_id, key, default=""):
        if self.boom:
            raise RuntimeError("settings unavailable")
        assert key == me.MAP_ENGINE_KEY
        return self.value if self.value is not None else default


@pytest.mark.asyncio
async def test_the_free_engine_is_never_handed_a_billable_key(monkeypatch):
    """A client on OpenStreetMap has no use for the key and no business
    holding one — loading it would bill a map view nobody drew."""
    monkeypatch.setenv(me.ENV_GOOGLE_KEY, "AIza-test")
    out = await me.for_account(1, _Tenant("osm"))
    assert out["engine"] == me.OSM
    assert "key" not in out


@pytest.mark.asyncio
async def test_the_google_engine_carries_the_key(monkeypatch):
    monkeypatch.setenv(me.ENV_GOOGLE_KEY, "AIza-test")
    out = await me.for_account(1, _Tenant("google"))
    assert out["engine"] == me.GOOGLE
    assert out["key"] == "AIza-test"


@pytest.mark.asyncio
async def test_it_says_what_was_asked_for_even_when_it_could_not_give_it():
    """So a settings page can say "Google, but this server has no key"
    instead of reading back as OpenStreetMap and looking like it failed
    to save."""
    out = await me.for_account(1, _Tenant("google"))
    assert out["requested"] == me.GOOGLE
    assert out["engine"] == me.OSM
    assert out["google_available"] is False


@pytest.mark.asyncio
async def test_an_unreadable_setting_still_draws_a_map():
    out = await me.for_account(1, _Tenant(boom=True))
    assert out["engine"] == me.OSM
