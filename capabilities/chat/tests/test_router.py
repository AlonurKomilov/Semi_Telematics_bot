"""Real signed-token requests through the production app and its middleware."""
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from adapters.storage import Role
from capabilities.permissions.roles import invalidate_permissions_cache
from interfaces.api import auth, deps
from tests._security import api_client, bearer

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


def headers(team, name='fleet', **claims):
    user = team.users[name]
    return bearer(user.id, team.account.id, user.role.value, telegram_id=user.telegram_id,
                  is_primary_owner=user.is_primary_owner, **claims)


@pytest_asyncio.fixture
async def client(team, monkeypatch):
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '0')
    monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '0')
    async with api_client(team.db, monkeypatch) as client:
        yield client


async def create(client, team, *, role_group=False):
    payload = {'title': 'HTTP group'}
    if role_group:
        payload.update(kind='roles', role_keys=['fleet', 'dispatcher'])
    response = await client.post('/api/chat/conversations', headers=headers(team), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def send(client, team, cid, *, who='fleet', key='client-request', **payload):
    response = await client.post(f'/api/chat/conversations/{cid}/messages', headers=headers(team, who),
        json={'body': 'HTTP message', 'client_message_id': key, **payload})
    return response


async def test_real_session_gate_and_both_mounted_prefixes(client, team):
    assert (await client.get('/api/chat/bootstrap')).status_code == 401
    for prefix in ('/api', '/api/v1'):
        response = await client.get(prefix + '/chat/bootstrap', headers=headers(team))
        assert response.status_code == 200, response.text
        assert response.json()['items'][0]['system_key'] == 'general'
        assert 'no-store' in response.headers['cache-control']
    await team.db.set_role_permissions(team.account.id, 'fleet', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    assert (await client.get('/api/chat/bootstrap', headers=headers(team))).status_code == 403


async def test_send_history_reply_edit_delete_read_draft_search_pin_over_http(client, team):
    c = await create(client, team)
    base = f"/api/chat/conversations/{c['id']}"
    first = await send(client, team, c['id'], mentions=[team.users['peer'].id])
    assert first.status_code == 200, first.text
    m = first.json()['message']
    assert (await send(client, team, c['id'])).status_code == 409  # mention list changed
    repeat = await send(client, team, c['id'], mentions=[team.users['peer'].id])
    assert repeat.json()['replayed'] is True
    reply = await send(client, team, c['id'], who='peer', key='reply', reply_to_id=m['id'])
    assert reply.json()['message']['reply']['body'] == 'HTTP message'
    assert (await client.put(base + f"/pins/{m['id']}?expected_version=1", headers=headers(team))).status_code == 200
    assert len((await client.get(base + '/pins', headers=headers(team, 'peer'))).json()['items']) == 1
    assert len((await client.get(base + '/search?q=HTTP', headers=headers(team))).json()['items']) == 2
    history = await client.get(base + '/messages?limit=1', headers=headers(team, 'peer'))
    assert history.status_code == 200 and history.json()['next_before'] == 2
    read = await client.put(base + '/read', headers=headers(team, 'peer'), json={'through_message_seq': 2})
    assert read.status_code == 200 and read.json()['last_read_message_seq'] == 2
    draft = await client.put(base + '/draft', headers=headers(team, 'peer'),
                             json={'body': 'Private draft', 'expected_version': 0, 'reply_to_id': m['id']})
    assert draft.status_code == 200 and draft.json()['version'] == 1
    state = await client.patch(base + '/state', headers=headers(team, 'peer'), json={'muted': True})
    assert state.status_code == 200 and state.json()['muted'] is True
    assert not (await client.get(base + '/state', headers=headers(team))).json()['muted']
    assert (await client.get(base + '/draft', headers=headers(team))).json()['body'] == ''
    edited = await client.patch(base + f"/messages/{m['id']}", headers=headers(team),
                                json={'body': 'Edited body', 'expected_version': 1})
    assert edited.status_code == 200 and edited.json()['version'] == 2
    assert (await client.patch(base + f"/messages/{m['id']}", headers=headers(team),
                               json={'body': 'Stale', 'expected_version': 1})).status_code == 409
    deleted = await client.delete(base + f"/messages/{m['id']}?expected_version=2", headers=headers(team))
    assert deleted.status_code == 200 and deleted.json()['body'] is None
    assert (await client.get(base + '/pins', headers=headers(team))).json()['items'] == []
    assert (await client.get(base + '/draft', headers=headers(team, 'peer'))).json()['reply'] == {'status': 'unavailable'}


async def test_group_management_and_owner_only_delegation_over_http(client, team):
    c = await create(client, team, role_group=True)
    base = f"/api/chat/conversations/{c['id']}"
    admin = await client.put(base + f"/admins/{team.users['peer'].id}", headers=headers(team),
                             json={'expected_version': 1, 'actions': ['manage_info']})
    assert admin.status_code == 200 and admin.json()['version'] == 2
    response = await client.patch(base, headers=headers(team, 'peer'), json={'expected_version': 2, 'changes': {'title': 'Renamed'}})
    assert response.status_code == 200 and response.json()['title'] == 'Renamed'
    escalated = await client.put(base + f"/admins/{team.users['peer'].id}", headers=headers(team, 'peer'),
                                 json={'expected_version': 3, 'actions': ['manage_settings']})
    assert escalated.status_code == 403
    response = await client.get(base + '/admins', headers=headers(team))
    assert response.status_code == 200 and response.json()['items'][0]['actions'] == ['manage_info']
    transferred = await client.post(base + '/transfer-ownership', headers=headers(team),
                                    json={'expected_version': 3, 'new_owner_id': team.users['peer'].id})
    assert transferred.status_code == 200 and transferred.json()['caller']['group_role'] == 'member'
    assert (await client.get(base + '/admins', headers=headers(team))).status_code == 403


async def test_all_content_endpoints_hide_another_account_or_another_dm(client, team):
    dm = await client.post('/api/chat/direct', headers=headers(team), json={'other_user_id': team.users['peer'].id})
    assert dm.status_code == 200
    cid = dm.json()['id']
    message = await send(client, team, cid)
    mid = message.json()['message']['id']
    other = await team.db.create_account('Foreign tenant')
    user = await team.db.create_user(99771, other.id, role=Role.OWNER)
    await team.db.set_role_permissions(other.id, 'owner', {'can_view_chat': True})
    foreign = bearer(user.id, other.id, 'owner', telegram_id=user.telegram_id)
    for denied in (foreign, headers(team, 'owner')):
        for suffix in ('', '/messages', f'/messages/{mid}', '/search?q=HTTP', '/pins', '/draft', '/state', '/members'):
            response = await client.get(f'/api/chat/conversations/{cid}' + suffix, headers=denied)
            assert response.status_code == 404, (suffix, response.text)
            assert 'HTTP message' not in response.text
        response = await client.post(f'/api/chat/conversations/{cid}/messages', headers=denied,
                                      json={'body': 'Injection', 'client_message_id': 'inject'})
        assert response.status_code == 404


@pytest.mark.parametrize('field,value', [('account_id', 99), ('author_id', 99), ('user_id', 99), ('caller', {'actions': ['send']})])
async def test_request_identity_and_actions_cannot_be_spoofed(client, team, field, value):
    c = await create(client, team)
    response = await send(client, team, c['id'], **{field: value})
    assert response.status_code == 422
    assert (await client.get(f"/api/chat/conversations/{c['id']}/messages", headers=headers(team))).json()['items'] == []


async def test_invalid_sizes_ids_and_blank_messages_fail_before_storage(client, team):
    c = await create(client, team)
    assert (await send(client, team, c['id'], body=' ')).status_code == 400
    assert (await send(client, team, c['id'], body='x' * 4001)).status_code == 400
    assert (await send(client, team, c['id'], mentions=[True])).status_code == 422
    assert (await client.get(f"/api/chat/conversations/{c['id']}/messages?limit=100000", headers=headers(team))).status_code == 422
    assert (await client.get(f"/api/chat/conversations/{c['id']}/messages/{2**64}", headers=headers(team))).status_code == 422


async def test_scoped_and_revoked_sessions_are_refused(client, team):
    scoped = headers(team, aud=auth.EXTENSION_AUDIENCE)
    assert (await client.get('/api/chat/bootstrap', headers=scoped)).status_code == 403
    old = headers(team)
    await team.db.update_user(team.users['fleet'].id, is_active=False)
    assert (await client.get('/api/chat/bootstrap', headers=old)).status_code == 401


@pytest.mark.parametrize('hold,expected', [('billing', 402), ('quarantine', 403)])
async def test_chat_requests_use_existing_account_enforcement(client, team, monkeypatch, hold, expected):
    from adapters.storage.billing import BillingMixin
    from system.security import quarantine
    c = await create(client, team)
    if hold == 'billing':
        monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '1')
        monkeypatch.setattr(team.db, 'get_subscription', AsyncMock(return_value={'status': 'past_due'}))
        monkeypatch.setattr(BillingMixin, 'is_account_blocked', staticmethod(lambda *args: (True, 'test hold')))
    else:
        monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '1')
        monkeypatch.setattr(quarantine, 'is_request_held', AsyncMock(return_value=True))
    response = await client.get('/api/chat/bootstrap', headers=headers(team))
    assert response.status_code == expected
    assert (await send(client, team, c['id'])).status_code == expected


async def test_people_and_recovery_are_minimal_through_http(client, team):
    c = await create(client, team, role_group=True)
    people = await client.get('/api/chat/people?conversation_id=' + c['id'], headers=headers(team))
    assert people.status_code == 200
    assert all(set(row) == {'id', 'display_name', 'role'} for row in people.json()['items'])
    await team.db.update_user(team.users['fleet'].id, is_active=False)
    recovery = await client.get('/api/chat/recovery', headers=headers(team, 'owner'))
    assert recovery.status_code == 200 and recovery.json()['items'][0]['id'] == c['id']
    assert 'HTTP group' not in recovery.text
    response = await client.post(f"/api/chat/conversations/{c['id']}/recover-ownership", headers=headers(team, 'owner'),
        json={'expected_version': 1, 'new_owner_id': team.users['peer'].id})
    assert response.status_code == 200
    assert set(response.json()) == {'id', 'owner_user_id', 'version'}


async def test_send_budget_is_per_authenticated_member(client, team):
    c = await create(client, team)
    for _ in range(60):
        response = await send(client, team, c['id'], key='same-safe-retry')
        assert response.status_code == 200, response.text
    assert (await send(client, team, c['id'], key='same-safe-retry')).status_code == 429
    assert (await send(client, team, c['id'], who='peer', key='peer-budget')).status_code == 200


async def test_cookie_login_fallback_uses_the_same_identity_for_chat(client, team):
    token = headers(team)['Authorization'][7:]
    response = await client.get('/api/chat/bootstrap', headers={
        'Cookie': f'{auth.AUTH_COOKIE_NAME}={token}', 'Authorization': 'Bearer broken-token'})
    assert response.status_code == 200
    assert response.json()['items'][0]['system_key'] == 'general'


async def test_edit_mention_picker_uses_target_history_over_http(client, team):
    c = await create(client, team, role_group=True)
    base = f"/api/chat/conversations/{c['id']}"
    changed = await client.patch(base, headers=headers(team), json={
        'expected_version': 1, 'changes': {'new_member_history': 'since_join'}})
    assert changed.status_code == 200
    m = (await send(client, team, c['id'])).json()['message']
    await team.db.update_user(team.users['peer'].id, is_active=False)
    await team.db.update_user(team.users['peer'].id, is_active=True)
    picker = await client.get(f"/api/chat/people?conversation_id={c['id']}&message_id={m['id']}", headers=headers(team))
    assert picker.status_code == 200 and [r['id'] for r in picker.json()['items']] == [team.users['fleet'].id]
    edit = await client.patch(base + f"/messages/{m['id']}", headers=headers(team),
        json={'expected_version': 1, 'body': 'Edited', 'mentions': [team.users['peer'].id]})
    assert edit.status_code == 400 and edit.json()['detail']['code'] == 'invalid_mention'


async def test_member_directory_exposes_group_roles_without_admin_rights_or_private_profile(client, team):
    c = await create(client, team, role_group=True)
    base = f"/api/chat/conversations/{c['id']}"
    uid = team.users['peer'].id
    assert (await client.put(base + f'/admins/{uid}', headers=headers(team),
                            json={'expected_version': 1, 'actions': ['manage_info']})).status_code == 200
    response = await client.get(base + '/members', headers=headers(team, 'peer'))
    assert response.status_code == 200
    rows = response.json()['items']
    assert {r['id']: r['group_role'] for r in rows} == {team.users['fleet'].id: 'owner', uid: 'admin'}
    assert all(set(r) == {'id', 'display_name', 'role', 'group_role'} for r in rows)
    assert (await client.get(base + '/admins', headers=headers(team, 'peer'))).status_code == 403
    assert (await client.get(base + '/members', headers=headers(team, 'owner'))).status_code == 404
    dm = await client.post('/api/chat/direct', headers=headers(team), json={'other_user_id': uid})
    assert (await client.get(f"/api/chat/conversations/{dm.json()['id']}/members", headers=headers(team, 'owner'))).status_code == 404


async def test_send_consumes_only_its_versioned_draft_even_if_response_is_lost(client, team):
    c = await create(client, team)
    base = f"/api/chat/conversations/{c['id']}"
    draft = await client.put(base + '/draft', headers=headers(team), json={'body': '  HTTP message  ', 'expected_version': 0})
    version = draft.json()['version']
    sent = await send(client, team, c['id'], draft_version=version)
    assert sent.status_code == 200
    assert sent.json()['draft']['body'] == '' and sent.json()['draft']['version'] == version + 1
    # A reload observes the atomic clear, even if the original send response never arrived.
    assert (await client.get(base + '/draft', headers=headers(team))).json()['body'] == ''
    await client.put(base + '/draft', headers=headers(team), json={'body': 'Next unsent message', 'expected_version': version + 1})
    retry = await send(client, team, c['id'], draft_version=version)
    assert retry.json()['replayed'] is True
    assert retry.json()['draft']['body'] == 'Next unsent message'
    assert len((await client.get(base + '/messages', headers=headers(team))).json()['items']) == 1


@pytest.mark.parametrize('employee_grant,manager_grant', [(True, False), (False, True)])
async def test_permissions_editor_session_and_chat_use_same_tier_grant(
    client, team, employee_grant, manager_grant,
):
    """A matrix save must agree with /me, preview rows and the Chat API."""
    manager = await team.db.create_user(88999, team.account.id, role=Role.FLEET, display_name='Manager')
    await team.db.update_user(manager.id, is_manager=True)
    team.users['manager'] = await team.db.get_user_by_id(manager.id)
    endpoint = '/api/admin/permissions/roles'
    for tier, granted in (('base', employee_grant), ('senior', manager_grant)):
        response = await client.put(endpoint, headers=headers(team, 'owner'), json={
            'role': 'fleet', 'tier': tier, 'permissions': {'can_view_chat': granted},
        })
        assert response.status_code == 200, response.text
    preview = await client.get(endpoint, headers=headers(team, 'owner'))
    assert preview.status_code == 200, preview.text
    current = preview.json()['current']
    assert current['fleet']['can_view_chat'] is employee_grant
    assert current['fleet__manager']['can_view_chat'] is manager_grant
    for who, granted in (('fleet', employee_grant), ('manager', manager_grant)):
        # Changing a member's tier rotates auth_version; sign a fresh session.
        session = headers(team, who, auth_version=team.users[who].auth_version)
        me = await client.get('/api/user/me', headers=session)
        assert me.status_code == 200, me.text
        assert me.json()['permissions']['can_view_chat'] is granted
        chat = await client.get('/api/chat/bootstrap', headers=session)
        assert chat.status_code == (200 if granted else 403), chat.text


@pytest.mark.parametrize('primary_grant,co_grant', [(True, False), (False, True)])
async def test_owner_tiers_match_me_and_chat_even_with_fleet_preview_header(
    client, team, primary_grant, co_grant,
):
    co = await team.db.create_user(88998, team.account.id, role=Role.OWNER, display_name='Co-owner')
    await team.db.update_user(co.id, is_primary_owner=False)
    await team.db.update_user(team.users['owner'].id, is_primary_owner=True)
    team.users['co'] = await team.db.get_user_by_id(co.id)
    team.users['owner'] = await team.db.get_user_by_id(team.users['owner'].id)
    owner_session = headers(team, 'owner', auth_version=team.users['owner'].auth_version)
    endpoint = '/api/admin/permissions/roles'
    for tier, granted in (('co', co_grant), (None, primary_grant)):
        response = await client.put(endpoint, headers=owner_session, json={
            'role': 'owner', 'tier': tier, 'permissions': {'can_view_chat': granted},
        })
        assert response.status_code == 200, response.text
    rows = (await client.get(endpoint, headers=owner_session)).json()['current']
    assert rows['owner']['can_view_chat'] is primary_grant
    assert rows['owner__co']['can_view_chat'] is co_grant
    for who, grant in (('owner', primary_grant), ('co', co_grant)):
        session = headers(team, who, auth_version=team.users[who].auth_version)
        session['X-View-As'] = 'fleet'  # Fleet has Chat; this never changes the actor.
        me = await client.get('/api/user/me', headers=session)
        assert me.status_code == 200, me.text
        assert me.json()['is_primary_owner'] is (who == 'owner')
        assert me.json()['permissions']['can_view_chat'] is grant
        response = await client.get('/api/chat/bootstrap', headers=session)
        assert response.status_code == (200 if grant else 403), response.text
