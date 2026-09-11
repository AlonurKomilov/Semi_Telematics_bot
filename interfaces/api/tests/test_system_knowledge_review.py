"""``/system/knowledge`` — the platform's half of the publishing decision.

A public knowledge-base article is readable by every account on the
platform and is fed to every account's AI assistant.  The publishing
account's own owner decides whether to SUBMIT; the operator decides
whether it goes out.  These tests drive that second gate through the
real ASGI app: only an operator may reach it, publishing is refused for
anything the account has not approved or that is quarantined, and the
effect is visible to a DIFFERENT account.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def system_app(pg_db, monkeypatch):
    db = pg_db
    publisher = await db.create_account("KB Publisher Co")
    reader = await db.create_account("KB Reader Co")
    op = await db.create_user(920001, publisher.id, role=Role.OWNER)
    non_op = await db.create_user(920002, reader.id, role=Role.OWNER)

    import capabilities.permissions.roles as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client, "db": db,
            "publisher": publisher.id, "reader": reader.id,
            "op": {"Authorization": f"Bearer {create_jwt(op.telegram_id, publisher.id, 'owner')}"},
            "non_op": {"Authorization": f"Bearer {create_jwt(non_op.telegram_id, reader.id, 'owner')}"},
        }


async def _submitted(db, account_id: int, title: str) -> int:
    """An article its own account has approved — i.e. waiting on us."""
    art = await db.add_kb_article(
        account_id=account_id, title=title, description="body",
        category="maintenance", visibility="public",
        created_by=7100, creator_name="Author",
    )
    await db.approve_kb_article(art)
    return art


async def _reader_sees(db, reader_id: int, title: str) -> bool:
    rows = await db.get_kb_articles(
        account_id=reader_id, user_role="owner", user_id=920002)
    return title in {r["title"] for r in rows}


@pytest.mark.asyncio
async def test_only_an_operator_reaches_the_queue(system_app):
    s = system_app
    assert (await s["client"].get("/api/system/knowledge/pending", headers=s["op"])).status_code == 200
    r = await s["client"].get("/api/system/knowledge/pending", headers=s["non_op"])
    assert r.status_code == 403
    assert (await s["client"].get("/api/system/knowledge/pending")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_queue_holds_what_an_account_submitted(system_app):
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Brake SOP")
    r = await s["client"].get("/api/system/knowledge/pending", headers=s["op"])
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert art in {a["id"] for a in body["articles"]}


@pytest.mark.asyncio
async def test_publishing_makes_it_visible_to_another_account(system_app):
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Published SOP")
    assert not await _reader_sees(s["db"], s["reader"], "Published SOP")

    r = await s["client"].post(
        f"/api/system/knowledge/{art}/approve", headers=s["op"], json={"note": "checked"})
    assert r.status_code == 200 and r.json()["platform_approved"] is True

    assert await _reader_sees(s["db"], s["reader"], "Published SOP")
    listed = (await s["client"].get("/api/system/knowledge/published", headers=s["op"])).json()
    assert art in {a["id"] for a in listed["articles"]}


@pytest.mark.asyncio
async def test_a_non_operator_cannot_publish(system_app):
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Blocked SOP")
    r = await s["client"].post(f"/api/system/knowledge/{art}/approve", headers=s["non_op"])
    assert r.status_code == 403
    assert not await _reader_sees(s["db"], s["reader"], "Blocked SOP")


@pytest.mark.asyncio
async def test_what_the_account_has_not_approved_cannot_be_published(system_app):
    """The operator blesses publication; they do not submit on an
    account's behalf."""
    s = system_app
    art = await s["db"].add_kb_article(
        account_id=s["publisher"], title="Draft SOP", description="body",
        visibility="public", created_by=7100, creator_name="A",
    )
    r = await s["client"].post(f"/api/system/knowledge/{art}/approve", headers=s["op"])
    assert r.status_code == 409
    assert "own account has approved" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_quarantined_article_is_refused_until_it_is_restored(system_app):
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Infected SOP")
    await s["db"].mark_article_quarantined(art, reason="EICAR-Test-Signature")
    r = await s["client"].post(f"/api/system/knowledge/{art}/approve", headers=s["op"])
    assert r.status_code == 409
    assert "quarantined" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unpublish_withdraws_it_and_keeps_the_reason(system_app):
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Withdrawn SOP")
    await s["client"].post(f"/api/system/knowledge/{art}/approve", headers=s["op"])
    assert await _reader_sees(s["db"], s["reader"], "Withdrawn SOP")

    r = await s["client"].post(
        f"/api/system/knowledge/{art}/unpublish", headers=s["op"],
        json={"note": "duplicates our own guide"})
    assert r.status_code == 200 and r.json()["ok"] is True

    assert not await _reader_sees(s["db"], s["reader"], "Withdrawn SOP")
    row = await s["db"].get_kb_article(art)
    # Refusing publication is not deleting somebody's work.
    assert row is not None and row["visibility"] == "private"
    assert row["platform_review_note"] == "duplicates our own guide"


@pytest.mark.asyncio
async def test_a_declined_article_keeps_its_trace_on_the_operator_surface(system_app):
    """A decision that leaves no trace on the surface that made it is not
    a reviewable decision — declining drops the row out of both the
    pending and published queries, so it needs its own."""
    s = system_app
    art = await _submitted(s["db"], s["publisher"], "Declined SOP")
    await s["client"].post(
        f"/api/system/knowledge/{art}/unpublish", headers=s["op"],
        json={"note": "duplicates our own guide"})

    r = await s["client"].get("/api/system/knowledge/refused", headers=s["op"])
    assert r.status_code == 200
    row = next((a for a in r.json()["articles"] if a["id"] == art), None)
    assert row is not None
    assert row["platform_review_note"] == "duplicates our own guide"

    # And it is in neither of the other two lanes.
    for lane in ("pending", "published"):
        listed = (await s["client"].get(f"/api/system/knowledge/{lane}", headers=s["op"])).json()
        assert art not in {a["id"] for a in listed["articles"]}


@pytest.mark.asyncio
async def test_the_refused_queue_is_operator_only(system_app):
    s = system_app
    r = await s["client"].get("/api/system/knowledge/refused", headers=s["non_op"])
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_missing_article_is_a_404_not_a_500(system_app):
    s = system_app
    for path in ("approve", "unpublish"):
        r = await s["client"].post(f"/api/system/knowledge/99999999/{path}", headers=s["op"])
        assert r.status_code == 404
