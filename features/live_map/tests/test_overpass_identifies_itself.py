"""We tell a volunteer mirror who we are, because it asks — and refuses.

The Overpass usage policy asks every client to identify itself.  We sent
aiohttp's default (`Python/3.12 aiohttp/3.x`), which is to say nothing,
and on 2026-09-15 lambert.openstreetmap.de started enforcing it.
Measured, the identical POST:

    no User-Agent  ->  HTTP 406 in 0.16s   (refused at the door)
    identified     ->  HTTP 200 in 0.5s    (real data)

Every query this host made was being turned away before Overpass saw
it, and the client reported that as "the map-data source is not
answering" — blaming a mirror for our own bad manners, which is this
codebase's favourite failure wearing yet another face.

A URL and no person.  The policy wants a way to reach whoever runs the
client; the domain is that, and an operator's email address does not
belong in a header sent to a third party.
"""
from __future__ import annotations

import asyncio

import pytest

from features.live_map.poi import overpass


@pytest.mark.asyncio
async def test_the_shared_session_carries_a_user_agent():
    session = await overpass._get_http_session()
    try:
        ua = session.headers.get("User-Agent")
        assert ua, (
            "the Overpass session sends no User-Agent — aiohttp's default "
            "is anonymous, and lambert answers that with HTTP 406 before "
            "the query is ever read")
        assert "aiohttp" not in ua.lower() and "python" not in ua.lower(), (
            f"still the library default: {ua!r}")
    finally:
        await overpass.close_http_session()


def test_it_names_a_way_to_reach_us_and_not_a_person():
    ua = overpass._USER_AGENT
    assert "4truck" in ua.lower(), f"nothing identifies the client: {ua!r}"
    assert "http" in ua.lower(), (
        f"no contact URL — the policy asks for a way to reach the operator: {ua!r}")
    assert "@" not in ua, (
        f"an email address is being sent to a third party in a header: {ua!r}")


def test_both_post_sites_go_through_the_shared_session():
    """The header is set ON THE SESSION, so a POST built any other way
    would be anonymous again and silently 406."""
    import inspect
    for fn in (overpass._fetch_overpass, overpass._overpass_post):
        src = inspect.getsource(fn)
        assert "_get_http_session()" in src, (
            f"{fn.__name__} does not use the shared session — whatever it "
            "posts will carry no User-Agent and be refused at the door")
        assert "aiohttp.ClientSession(" not in src, (
            f"{fn.__name__} builds its own session, which will not have the "
            "User-Agent the shared one sets")
