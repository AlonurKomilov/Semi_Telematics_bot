"""Two accounts, one conversation: the other account does not exist to it.

Every chat test builds ONE account and proves its rules inside it — group
owners, admins, direct-message peers, posting modes.  None of them ask
the question a multi-tenant product is judged on first: can a user from
account B reach account A's conversation by naming it?  With the
structural tenant check deleted from access.py, all twenty-five of the
security-marked access tests stayed green, because nothing ever tried.

What holds the property is not a line of Python.  Three code walls
exist — ``chat_get_conversation`` is keyed by account, ``structurally_
eligible`` compares account ids, ``chat_membership`` is keyed by account
— and with all three removed at once these tests STAY GREEN, because the
schema refuses the state they would need: a membership row pairing A's
conversation with B's user cannot be written past the composite foreign
key ``(account_id, user_id) REFERENCES users(account_id, id)``, and B is
never projected into A's groups.  So this is a property guard for the
day somebody adds a lookup that is not keyed by account, and its
positive control below is what makes it discriminating: the same code
path answers 200 to a user who IS in the account.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from adapters.storage import Role
from capabilities.permissions.roles import invalidate_permissions_cache
from interfaces.api import deps
from tests._security import api_client, bearer

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


def _headers(user, account_id):
    return bearer(user.id, account_id, user.role.value, telegram_id=user.telegram_id,
                  is_primary_owner=user.is_primary_owner)


@pytest_asyncio.fixture
async def two_tenants(team, monkeypatch):
    """Account A is the shared ``team``; account B is a stranger with the
    same grant, so the only thing standing between B and A's data is the
    tenant wall."""
    db = team.db
    other = await db.create_account('Other Tenant Co')
    stranger = await db.create_user(89001, other.id, role=Role.OWNER, display_name='stranger')
    await db.set_role_permissions(other.id, Role.OWNER.value, {'can_view_chat': True})
    invalidate_permissions_cache(other.id)
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '0')
    monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '0')
    async with api_client(db, monkeypatch) as client:
        yield SimpleNamespace(client=client, a=team, b_account=other, b_user=stranger)


async def test_a_conversation_is_invisible_and_unreachable_across_the_wall(two_tenants):
    t = two_tenants
    a_hdr = _headers(t.a.users['fleet'], t.a.account.id)
    b_hdr = _headers(t.b_user, t.b_account.id)

    made = await t.client.post('/api/chat/conversations', headers=a_hdr, json={'title': 'A only'})
    assert made.status_code == 200, made.text
    cid = made.json()['id']
    sent = await t.client.post(f'/api/chat/conversations/{cid}/messages', headers=a_hdr,
                               json={'body': 'for A', 'client_message_id': 'a-1'})
    assert sent.status_code == 200, sent.text

    # B names A's conversation by id: every door answers "no such thing"
    for method, path, body in (
        ('GET',    f'/api/chat/conversations/{cid}', None),
        ('GET',    f'/api/chat/conversations/{cid}/messages', None),
        ('GET',    f'/api/chat/conversations/{cid}/members', None),
        ('POST',   f'/api/chat/conversations/{cid}/messages',
                   {'body': 'from B', 'client_message_id': 'b-1'}),
        ('PATCH',  f'/api/chat/conversations/{cid}',
                   {'expected_version': 1, 'changes': {'title': 'taken'}}),
        ('PUT',    f'/api/chat/conversations/{cid}/read', {'through_message_seq': 1}),
    ):
        r = await t.client.request(method, path, headers=b_hdr, json=body)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        # and the refusal says nothing that would tell B it guessed right
        assert 'for A' not in r.text and 'A only' not in r.text

    # B's own bootstrap and sync never list it
    boot = await t.client.get('/api/chat/bootstrap', headers=b_hdr)
    assert boot.status_code == 200, boot.text
    assert cid not in boot.text
    sync = await t.client.get('/api/chat/sync', headers=b_hdr)
    assert sync.status_code == 200, sync.text
    assert cid not in sync.text and 'for A' not in sync.text

    # A's message is exactly where A left it
    mine = await t.client.get(f'/api/chat/conversations/{cid}/messages', headers=a_hdr)
    assert mine.status_code == 200 and 'for A' in mine.text

    # POSITIVE CONTROL — the same path, a user who is IN the account:
    # A's general room answers 200 to A's owner and 404 to B.  Without
    # this the 404s above could be a broken route, not a wall.
    boot_a = await t.client.get('/api/chat/bootstrap', headers=a_hdr)
    general = next(i for i in boot_a.json()['items'] if i.get('system_key') == 'general')
    ok = await t.client.get(f"/api/chat/conversations/{general['id']}",
                            headers=_headers(t.a.users['owner'], t.a.account.id))
    assert ok.status_code == 200, ok.text
    no = await t.client.get(f"/api/chat/conversations/{general['id']}", headers=b_hdr)
    assert no.status_code == 404, no.text


async def test_a_direct_message_cannot_be_opened_to_a_user_in_another_account(two_tenants):
    """The other door in: not reading A's rooms, but pulling A's PEOPLE
    into B's.  A direct conversation names two user ids, and the second
    one must be from the same account or it is nobody."""
    t = two_tenants
    b_hdr = _headers(t.b_user, t.b_account.id)
    a_peer = t.a.users['peer']
    r = await t.client.post('/api/chat/direct', headers=b_hdr, json={'other_user_id': a_peer.id})
    assert r.status_code in (403, 404), (r.status_code, r.text)
    people = await t.client.get('/api/chat/people', headers=b_hdr)
    assert people.status_code == 200, people.text
    assert 'peer' not in people.text and f'"id": {a_peer.id}' not in people.text, \
        "A's people are not in B's picker"
