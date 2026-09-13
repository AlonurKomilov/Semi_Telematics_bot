"""Why the panel's Google tiles come through us, and what must stay true.

The platform's Google key is protected by an HTTP-referrer restriction
rather than by secrecy — every tile request carries the key, so the
restriction is the only thing between it and anyone who opens a network
tab.  That has a hard edge: a client whose page is not on an allowed
origin sends NO ``Referer`` header, and Google answers

    403  Requests from referer <empty> are blocked.

A browser-extension page is exactly that case; Chrome never sends a
``chrome-extension://`` origin to an https host.  The panel opened a
valid session, drew Google's attribution, and rendered a grey
rectangle — nothing in it was wrong, the request simply could not
succeed from there.  Measured against the live API on 2026-09-13: with
the header 200 image/png, without it 403, with a wrong one 403.

The alternative was to teach the extension to forge the header, which
would have meant shipping a header-rewriting rule and the key to every
install — together, an unrestricted key for anybody who read both.

These tests hold the shape of the answer, not Google's behaviour.
"""
from __future__ import annotations

import inspect

from features.location import map_engine, router as map_router


def test_the_session_wire_offers_both_paths():
    """Direct for a page on an allowed origin, proxied for one that is
    not.  Dropping either is a regression: the dashboard would start
    paying our bandwidth, or the panel would go grey again."""
    wire = map_engine.tile_wire(
        "roadmap",
        {"session": "S", "expiry": 1, "tile_size": 256, "image_format": "png"},
        "K",
    )
    assert wire["tile_url"].startswith("https://tile.googleapis.com/")
    assert wire["proxy_tile_url"].startswith("/map/tile?")
    assert wire["proxy_copyright_url"].startswith("/map/tile-copyright?")
    # Leaflet's placeholders, spelled the way Leaflet expands them.
    for token in ("{z}", "{x}", "{y}"):
        assert token in wire["proxy_tile_url"], token


def test_the_proxy_template_carries_no_key():
    """The whole point: the key stays on this side of the wire."""
    wire = map_engine.tile_wire(
        "satellite",
        {"session": "S", "expiry": 1, "tile_size": 256, "image_format": "png"},
        "SECRET-KEY",
    )
    assert "SECRET-KEY" not in wire["proxy_tile_url"]
    assert "SECRET-KEY" not in wire["proxy_copyright_url"]
    # …and the direct one still does, because Google requires it there.
    assert "SECRET-KEY" in wire["tile_url"]


def test_the_server_sends_the_referer_on_every_google_call():
    """All three: the session, the tile and the copyright line.  A call
    that forgets it gets the same 403 the browser got, and the symptom
    is a blank map rather than an error anybody reads."""
    for fn in (map_engine._create_session, map_engine.fetch_tile,
               map_engine.fetch_copyright):
        src = inspect.getsource(fn)
        assert "SESSION_REFERER" in src, fn.__name__


def test_a_tile_is_refused_to_an_account_that_did_not_choose_google():
    """`can_view_location` says this person may see a map.  It does not
    say the account agreed to pay Google for one, and tiles are billed
    per request against our project."""
    src = inspect.getsource(map_router._require_google)
    assert "GOOGLE" in src
    assert "403" in src or "status_code=403" in src


def test_the_engine_check_is_cached_so_a_drag_is_not_hundreds_of_db_reads():
    """One view is a dozen tiles and a drag is hundreds.  A tenant-DB
    read per tile would make the fix cost more than the bug."""
    assert map_router._ENGINE_TTL_S > 0
    src = inspect.getsource(map_router._require_google)
    assert "_engine_seen" in src


def test_the_server_may_carry_a_key_of_its_own(monkeypatch):
    """The browser's key is public by design; the server's need not be.

    A referrer restriction stops a casual reader and nobody else — a
    forged referer was measured returning 200 from this very server —
    so the proxy takes an IP-restricted key when the deployment has
    been given one, and falls back to the shared key when it has not.
    """
    monkeypatch.delenv(map_engine.ENV_GOOGLE_TILE_KEY, raising=False)
    monkeypatch.setenv(map_engine.ENV_GOOGLE_KEY, "PUBLIC")
    assert map_engine.tile_key() == "PUBLIC", "must not break a deployment without one"
    monkeypatch.setenv(map_engine.ENV_GOOGLE_TILE_KEY, "SERVER-ONLY")
    assert map_engine.tile_key() == "SERVER-ONLY"
    # …and the browser's half is untouched: the dashboard still fetches
    # tiles directly and needs the public key in its template.
    assert map_engine.google_key() == "PUBLIC"


def test_a_session_belongs_to_the_key_that_opened_it(monkeypatch):
    """Two keys can be in play at once.  Whether a session opened under
    one is accepted on a tile request carrying the other is undocumented
    and not worth discovering in production, so the cache is keyed by
    both.  Sessions are free of quota and last a fortnight."""
    import asyncio

    map_engine.reset_sessions_for_tests()
    opened: list[str] = []

    async def fake_create(map_type: str, key: str) -> dict:
        opened.append(key)
        return {"session": f"S-{key}", "expiry": 2 ** 31,
                "tile_size": 256, "image_format": "png"}

    monkeypatch.setattr(map_engine, "_create_session", fake_create)
    a = asyncio.run(map_engine.tile_session("roadmap", "KEY-A"))
    b = asyncio.run(map_engine.tile_session("roadmap", "KEY-B"))
    again = asyncio.run(map_engine.tile_session("roadmap", "KEY-A"))
    assert a["session"] != b["session"], "one session was shared across two keys"
    assert again["session"] == a["session"], "the cache stopped caching"
    assert opened == ["KEY-A", "KEY-B"], opened
    map_engine.reset_sessions_for_tests()


def test_the_proxy_does_not_take_the_browsers_key_from_its_caller():
    """The route used to hand `google_key()` in.  It no longer passes
    one at all, so there is a single place that decides which key the
    server presents."""
    src = inspect.getsource(map_router.map_tile)
    assert "google_key()" not in src
    assert "fetch_tile(type, z, x, y)" in src


def test_both_proxy_routes_are_reachable_by_a_panel_token():
    """A route the token may DO something on but may not KNOCK on is a
    403 with a confusing message."""
    from interfaces.api.auth import EXTENSION_ROUTES
    assert "/map/tile" in EXTENSION_ROUTES
    assert "/map/tile-copyright" in EXTENSION_ROUTES
