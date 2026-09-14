"""A tenant's ``media_url`` reaches the operator console as an href.

The allowlist validator refuses every scheme but https.  It was skipped
for "internal paths" — and the test for an internal path was "anything
that does not start with http(s)".  ``javascript:`` is not http(s).  So
a self-serve owner could store one, approve their own public article,
and wait for the platform review queue to render it as a link on the
console origin.

What these pin: an internal path is a PATH — no scheme, no
protocol-relative prefix, no whitespace a browser would strip inside a
scheme — so everything else meets the validator and its 422; a real
upload path is still stored verbatim; and the console only links an
https value, showing every other stored string as text.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from features.knowledge.router import _is_internal_kb_path
from tests._repo import REPO

INTERNAL = "data/userdata/account-1/_account/knowledge/manual.pdf"


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "JavaScript:alert(1)", "java\tscript:alert(1)",
    "java\nscript:alert(1)", " javascript:alert(1)", "data:text/html,<script>",
    "vbscript:msgbox", "file:///etc/passwd", "//evil.example/x",
    "http://evil.example/x", "https://youtube.com/watch?v=1",
])
def test_anything_with_a_scheme_is_not_an_internal_path(value):
    assert _is_internal_kb_path(value) is False, value


@pytest.mark.parametrize("value", [INTERNAL, "account-7/_account/knowledge/a.png", "/srv/store/account-7/x.pdf"])
def test_a_path_is_an_internal_path(value):
    assert _is_internal_kb_path(value) is True, value


@pytest_asyncio.fixture
async def owner_app(pg_db, monkeypatch):
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    db = pg_db
    acct = await db.create_account("Scheme Co")
    owner = await db.create_user(950001, acct.id, role=Role.OWNER)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    async with AsyncClient(transport=ASGITransport(app=create_api()), base_url="http://testserver") as client:
        yield {"client": client, "db": db, "acct": acct, "tg": owner.telegram_id, "uid": owner.id,
               "hdr": {"Authorization": f"Bearer {create_jwt(owner.telegram_id, acct.id, 'owner')}"}}


def _article(**over):
    return {"title": "How to", "description": "body", "category": "general",
            "media_type": "link", "visibility": "private", **over}


@pytest.mark.asyncio
async def test_a_javascript_url_is_refused_on_create_and_on_update(owner_app):
    s = owner_app
    r = await s["client"].post("/api/knowledge/articles", headers=s["hdr"],
                               json=_article(media_url="javascript:alert(document.cookie)"))
    assert r.status_code == 422, r.text
    assert "https://" in r.json()["detail"]
    # the tab-split spelling the browser would still execute
    r = await s["client"].post("/api/knowledge/articles", headers=s["hdr"],
                               json=_article(media_url="java\tscript:alert(1)"))
    assert r.status_code == 422, r.text
    rows = await s["db"].get_kb_articles(account_id=s["acct"].id, user_role="owner", user_id=s["tg"])
    assert rows == [], "nothing was stored"

    # a clean article cannot be turned into one after the fact either
    art = await s["db"].add_kb_article(
        account_id=s["acct"].id, title="Clean", description="body", category="general",
        visibility="private", created_by=s["uid"], creator_name="Owner", media_url=INTERNAL)
    r = await s["client"].put(f"/api/knowledge/articles/{art}", headers=s["hdr"],
                              json={"media_url": "javascript:alert(1)"})
    assert r.status_code == 422, r.text
    kept = [a for a in await s["db"].get_kb_articles(account_id=s["acct"].id, user_role="owner", user_id=s["tg"])
            if a["id"] == art][0]
    assert kept["media_url"] == INTERNAL


@pytest.mark.asyncio
async def test_a_real_upload_path_is_still_stored_verbatim(owner_app):
    s = owner_app
    r = await s["client"].post("/api/knowledge/articles", headers=s["hdr"],
                               json=_article(media_url=INTERNAL, media_type="pdf"))
    assert r.status_code in (200, 201), r.text
    rows = await s["db"].get_kb_articles(account_id=s["acct"].id, user_role="owner", user_id=s["tg"])
    assert [a["media_url"] for a in rows] == [INTERNAL]


def test_the_operator_console_links_only_an_https_value():
    """The sink: a stored string becomes an href only behind the https
    test.  A bare ``href={a.media_url}`` outside that gate is the bug."""
    src = (REPO / "interfaces/system_dashboard/src/pages/Knowledge.tsx").read_text()
    assert "isExternalHttps(a.media_url)" in src
    assert "/^https:\\/\\//i" in src
    body = src.split("isExternalHttps(a.media_url) ? (", 1)[1].split(") : (", 1)[0]
    assert "href={a.media_url}" in body, "the link lives inside the gate"
    assert src.count("href={a.media_url}") == 1, "and nowhere else"
