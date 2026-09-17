"""Security changes invalidate both protected requests and token refresh."""

from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api import auth, deps

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


def token_for(member, **overrides):
    token = auth.create_jwt(member.telegram_id, member.account_id, member.role.value,
                            user_id=member.id, is_primary_owner=member.is_primary_owner,
                            is_manager=member.is_manager, remember_me=True)
    payload = auth.decode_jwt(token)
    payload['auth_version'] = getattr(member, 'auth_version', 0)
    payload.update(overrides)
    return auth.jwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)


def app_for(db, monkeypatch):
    import infra.platform as platform
    monkeypatch.setattr(platform, '_db', db)
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    app = FastAPI()
    app.include_router(auth.router, prefix='/api')

    @app.get('/api/privileged')
    async def privileged(user=Depends(deps.require_permission('can_manage_users'))):
        return {'role': user['role']}

    @app.get('/api/identity')
    async def identity(user=Depends(deps.get_current_user)):
        return {'role': user['role'], 'account_id': user['account_id']}

    return app


@pytest.mark.parametrize('change', ['demotion', 'deactivation', 'password', 'manager', 'owner_flag'])
async def test_security_change_revokes_old_token_and_refresh(pg_db, monkeypatch, change):
    account = await pg_db.create_account('Session security')
    member = await pg_db.create_user(900031, account.id, role=Role.OWNER)
    old = token_for(member)
    app = app_for(pg_db, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        headers = {'Authorization': f'Bearer {old}'}
        assert (await client.get('/api/privileged', headers=headers)).status_code == 200
        if change == 'demotion':
            await pg_db.update_user(member.id, role='driver', is_primary_owner=False)
        elif change == 'deactivation':
            await pg_db.update_user(member.id, is_active=False)
        elif change == 'password':
            await pg_db.set_user_email_password(member.id, 'review@example.invalid', 'synthetic-password-hash')
        elif change == 'manager':
            await pg_db.update_user(member.id, is_manager=True)
        else:
            await pg_db.update_user(member.id, is_primary_owner=False)
        assert (await client.get('/api/identity', headers=headers)).status_code == 401
        assert (await client.post('/api/auth/refresh', headers=headers)).status_code == 401
        changed = await pg_db.get_user(member.id)
        assert changed.auth_version == member.auth_version + 1


async def test_current_role_wins_even_if_signed_claim_is_stale(pg_db, monkeypatch):
    account = await pg_db.create_account('Current role')
    member = await pg_db.create_user(900032, account.id, role=Role.DRIVER)
    stale = token_for(member, role='owner', is_primary_owner=True, is_manager=True)
    app = app_for(pg_db, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/privileged', headers={'Authorization': f'Bearer {stale}'})
        assert response.status_code == 403


async def test_token_cannot_name_another_account(pg_db, monkeypatch):
    account = await pg_db.create_account('A')
    other = await pg_db.create_account('B')
    member = await pg_db.create_user(900033, account.id, role=Role.OWNER)
    app = app_for(pg_db, monkeypatch)
    token = token_for(member, account_id=other.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/identity', headers={'Authorization': f'Bearer {token}'})).status_code == 401


async def test_recorded_revocation_survives_redis_outage(pg_db, monkeypatch):
    account = await pg_db.create_account('Durable revoke')
    member = await pg_db.create_user(900034, account.id, role=Role.OWNER)
    token = token_for(member)
    claims = auth.decode_jwt(token)
    sid = await pg_db.create_user_session(user_id=member.id, jti=claims['jti'],
        device_label='Test', user_agent='Test', ip='127.0.0.1', created_at=pg_db._now(),
        last_seen=pg_db._now(), expires_at='2099-01-01T00:00:00+00:00')
    app = app_for(pg_db, monkeypatch)
    monkeypatch.setattr('infra.cache.exists', AsyncMock(return_value=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        headers = {'Authorization': f'Bearer {token}'}
        assert (await client.get('/api/identity', headers=headers)).status_code == 200
        await pg_db.revoke_user_session(sid, owning_user_id=member.id)
        assert (await client.get('/api/identity', headers=headers)).status_code == 401
        assert (await client.post('/api/auth/refresh', headers=headers)).status_code == 401


async def test_legacy_token_requires_signin_and_email_only_refresh_works(pg_db, monkeypatch):
    account = await pg_db.create_account('Email identity')
    member = await pg_db.create_user_with_email('person@example.invalid', 'hash', account.id, role=Role.OWNER)
    app = app_for(pg_db, monkeypatch)
    token = token_for(member)
    legacy = auth.decode_jwt(token)
    legacy.pop('auth_version')
    legacy = auth.jwt.encode(legacy, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/identity', headers={'Authorization': f'Bearer {legacy}'})).status_code == 401
        response = await client.post('/api/auth/refresh', headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 200
        assert auth.decode_jwt(response.json()['access_token'])['uid'] == member.id


async def test_password_reset_revokes_all_devices_through_http(pg_db, monkeypatch):
    account = await pg_db.create_account('Reset devices')
    member = await pg_db.create_user_with_email('reset@example.invalid', auth._hash_password('Before!Password42'), account.id)
    reset = await pg_db.create_password_reset_token(member.id)
    tokens = [token_for(member), token_for(member)]
    apps = [app_for(pg_db, monkeypatch), app_for(pg_db, monkeypatch)]
    async with AsyncClient(transport=ASGITransport(app=apps[0]), base_url='http://test') as client:
        response = await client.post('/api/auth/reset-password', json={'token': reset, 'password': 'After!Password42'})
        assert response.status_code == 200
    for app, token in zip(apps, tokens):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/identity', headers={'Authorization': f'Bearer {token}'})).status_code == 401


async def test_login_requires_recording_and_rejects_stale_identity_snapshot(pg_db, monkeypatch):
    from starlette.requests import Request
    from fastapi import HTTPException
    account = await pg_db.create_account('Issuance')
    member = await pg_db.create_user(900043, account.id)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/auth/login', 'headers': []})
    monkeypatch.setattr(pg_db, 'create_user_session', AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await auth.mint_session_token(pg_db, request, user_id=member.id,
            telegram_id=member.telegram_id, account_id=account.id, role=member.role.value,
            auth_version=member.auth_version, remember_me=False)
    assert caught.value.status_code == 503
    await pg_db.set_user_email_password(member.id, 'changed@example.invalid', 'new-hash')
    with pytest.raises(HTTPException) as caught:
        await auth.mint_session_token(pg_db, request, user_id=member.id,
            telegram_id=member.telegram_id, account_id=account.id, role=member.role.value,
            auth_version=member.auth_version, remember_me=False)
    assert caught.value.status_code == 401


async def test_bot_approval_becomes_a_recorded_browser_session(pg_db, monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    account = await pg_db.create_account('Bot approval')
    member = await pg_db.create_user(900044, account.id)
    app_for(pg_db, monkeypatch)
    monkeypatch.setattr('infra.cache.get', AsyncMock(return_value={
        'status': 'approved', 'access_token': token_for(member), 'user': {'account_id': account.id}}))
    monkeypatch.setattr('infra.cache.delete', AsyncMock())
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/auth/bot-login/check/synthetic', 'headers': []})
    out = await auth.bot_login_check.__wrapped__(request, Response(), 'synthetic')
    claims = auth.decode_jwt(out['access_token'])
    sessions = await pg_db.list_user_sessions(member.id)
    assert any(row['jti'] == claims['jti'] for row in sessions)


async def test_rowless_operator_is_allowlisted_and_cannot_enter_customer_routes(monkeypatch):
    from starlette.requests import Request
    from fastapi import HTTPException
    monkeypatch.setattr('capabilities.permissions.roles.is_system_owner', lambda tid: tid == 900045)
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    token = auth.create_jwt(900045, 0, 'owner')
    async def current(path, credential):
        request = Request({'type': 'http', 'method': 'GET', 'path': path, 'headers': []})
        return await deps.get_current_user(request, authorization=f'Bearer {credential}', auth_token=None)
    assert (await current('/api/system/accounts', token))['sub'] == '900045'
    with pytest.raises(HTTPException) as caught:
        await current('/api/identity', token)
    assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        await current('/api/system/accounts', auth.create_jwt(900046, 0, 'owner'))
    assert caught.value.status_code == 401
