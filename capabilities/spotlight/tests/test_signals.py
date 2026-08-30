"""The spotlight signals endpoint — self-scoped, allowlisted, honest.

The contract worth pinning is the privacy one: this endpoint lets a
page ask "what have I done here?" and can NEVER be bent into "what
have they done" — there is no parameter for another user, and the
isolation test proves two users' counts cannot bleed.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def api(pg_db):
    db = pg_db
    acct = await db.create_account("Signals Co")
    a = await db.create_user(980001, acct.id, role=Role.FLEET)
    b = await db.create_user(980002, acct.id, role=Role.FLEET)

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t",
    ) as client:
        yield {
            "client": client, "db": db, "acct": acct,
            "a": a, "b": b,
            "tok_a": create_jwt(a.telegram_id, acct.id, "fleet"),
            "tok_b": create_jwt(b.telegram_id, acct.id, "fleet"),
        }
    _cp._db = _old


def _hdr(t): return {"Authorization": f"Bearer {t}"}


async def _seed(db, acct_id, actor_id, n_solo=0, n_grouped=0):
    events = [{
        "entity_type": "maintenance_task", "entity_id": f"s{i}",
        "action": "create", "changes": {}, "actor_user_id": actor_id,
        "group_id": None, "context": {},
    } for i in range(n_solo)]
    events += [{
        "entity_type": "maintenance_task", "entity_id": f"g{i}",
        "action": "create", "changes": {}, "actor_user_id": actor_id,
        "group_id": "grp-1", "context": {},
    } for i in range(n_grouped)]
    if events:
        await db.append_activity_events(acct_id, events)


@pytest.mark.asyncio
async def test_counts_split_solo_from_grouped(api):
    s = api
    await _seed(s["db"], s["acct"].id, s["a"].id, n_solo=6, n_grouped=3)
    r = await s["client"].get(
        "/api/me/spotlight-signals?pairs=maintenance_task:create",
        headers=_hdr(s["tok_a"]),
    )
    assert r.status_code == 200
    sig = r.json()["signals"]["maintenance_task:create"]
    assert sig == {"total": 9, "solo": 6, "grouped": 3}


@pytest.mark.asyncio
async def test_a_user_only_ever_sees_their_own_actions(api):
    """The privacy contract.  B's furious week of task-creating must be
    invisible to A — same account, same table, zero bleed."""
    s = api
    await _seed(s["db"], s["acct"].id, s["b"].id, n_solo=20)
    r = await s["client"].get(
        "/api/me/spotlight-signals?pairs=maintenance_task:create",
        headers=_hdr(s["tok_a"]),
    )
    assert r.json()["signals"]["maintenance_task:create"]["total"] == 0


@pytest.mark.asyncio
async def test_unknown_pair_is_refused_not_zeroed(api):
    """Silence would teach an author the signal is always zero; the 400
    teaches them the allowlist exists."""
    s = api
    r = await s["client"].get(
        "/api/me/spotlight-signals?pairs=users:delete",
        headers=_hdr(s["tok_a"]),
    )
    assert r.status_code == 400
    assert "ALLOWED_SIGNALS" in r.json()["detail"]


@pytest.mark.asyncio
async def test_requires_auth(api):
    r = await api["client"].get(
        "/api/me/spotlight-signals?pairs=maintenance_task:create")
    assert r.status_code in (401, 403)


def test_dashboard_tours_ask_only_allowlisted_signals():
    """The frontend's tour data may not request a pair the backend
    would refuse — parsed from the source, the callouts way."""
    import re
    from capabilities.spotlight import ALLOWED_SIGNALS
    from tests._repo import REPO
    allowed = {f"{e}:{a}" for e, a in ALLOWED_SIGNALS}
    offenders = []
    feat_dir = REPO / "interfaces" / "dashboard" / "src" / "features"
    for f in feat_dir.rglob("spotlights.ts"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"signals:\s*\[([^\]]*)\]", text):
            for pair in re.findall(r"'([^']+)'", m.group(1)):
                if pair not in allowed:
                    offenders.append(f"{f.name}: {pair}")
    assert not offenders, (
        "tour data requests signals the backend refuses: " + str(offenders))
