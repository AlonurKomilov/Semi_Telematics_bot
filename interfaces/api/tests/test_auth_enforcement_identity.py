"""Enforcement and the endpoint must authenticate the same session."""

from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from interfaces.api import auth, deps
from interfaces.api.app import BillingEnforcementMiddleware, QuarantineMiddleware
from system.security import quarantine

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


@pytest.mark.parametrize("middleware,blocked_status", [
    (QuarantineMiddleware, 403), (BillingEnforcementMiddleware, 402),
])
@pytest.mark.parametrize("bearer_kind", ["invalid", "expired", "revoked"])
async def test_blocked_cookie_cannot_be_rescued_by_another_bearer(
    pg_db, monkeypatch, middleware, blocked_status, bearer_kind,
):
    import infra.platform as platform
    from adapters.storage.billing import BillingMixin
    monkeypatch.setattr(platform, "_db", pg_db)
    monkeypatch.setattr(deps, "_fire_heartbeats", lambda _: None)
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    monkeypatch.setenv("BILLING_ENFORCEMENT_ENABLED", "1")
    account = await pg_db.create_account("Blocked")
    member = await pg_db.create_user(900010, account.id)
    cookie = auth.create_jwt(member.telegram_id, account.id, member.role.value, user_id=member.id)
    other = await pg_db.create_account("Unblocked")
    other_member = await pg_db.create_user(900011, other.id)
    bearer = auth.create_jwt(other_member.telegram_id, other.id, other_member.role.value,
                             user_id=other_member.id, jti="old-other-session")
    if bearer_kind == "invalid":
        bearer = "invalid-token"
    elif bearer_kind == "expired":
        payload = auth.decode_jwt(bearer)
        payload["exp"] = 1
        bearer = auth.jwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    else:
        monkeypatch.setattr(deps, "is_jti_revoked", AsyncMock(side_effect=lambda jti: jti == "old-other-session"))
    monkeypatch.setattr(quarantine, "is_request_held", AsyncMock(
        side_effect=lambda uid, aid: aid == account.id))
    monkeypatch.setattr(pg_db, "get_subscription", AsyncMock(
        side_effect=lambda aid: {"blocked": aid == account.id}))
    monkeypatch.setattr(BillingMixin, "is_account_blocked",
                        staticmethod(lambda sub, grace: (sub["blocked"], "test")))
    app = FastAPI()
    app.add_middleware(middleware)

    @app.get("/api/protected")
    async def protected(user=Depends(deps.get_current_user)):
        return {"account_id": user["account_id"]}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Cookie": f"{auth.AUTH_COOKIE_NAME}={cookie}"}
        assert (await client.get("/api/protected", headers=headers)).status_code == blocked_status
        response = await client.get("/api/protected", headers={**headers, "Authorization": f"Bearer {bearer}"})
        assert response.status_code == blocked_status



async def test_healthy_cookie_fallback_is_shared_by_both_middlewares(pg_db, monkeypatch):
    import infra.platform as platform
    from adapters.storage.billing import BillingMixin
    monkeypatch.setattr(platform, '_db', pg_db)
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '1')
    monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '1')
    account = await pg_db.create_account('Healthy fallback')
    member = await pg_db.create_user(900047, account.id)
    token = auth.create_jwt(member.telegram_id, account.id, member.role.value, user_id=member.id)
    reader = AsyncMock(side_effect=pg_db.get_user_auth_state)
    monkeypatch.setattr(pg_db, 'get_user_auth_state', reader)
    hold = AsyncMock(return_value=False)
    monkeypatch.setattr(quarantine, 'is_request_held', hold)
    monkeypatch.setattr(pg_db, 'get_subscription', AsyncMock(return_value={}))
    monkeypatch.setattr(BillingMixin, 'is_account_blocked', staticmethod(lambda sub, grace: (False, '')))
    app = FastAPI()
    app.add_middleware(BillingEnforcementMiddleware)
    app.add_middleware(QuarantineMiddleware)

    @app.get('/api/v1/protected')
    async def protected(user=Depends(deps.get_current_user)):
        return {'account_id': user['account_id']}

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/protected', headers={
            'Cookie': f'{auth.AUTH_COOKIE_NAME}={token}', 'Authorization': 'Bearer stale-token'})
        assert response.status_code == 200
        assert response.json()['account_id'] == account.id
    assert reader.await_count == 1
    hold.assert_awaited_once_with(member.id, account.id)
