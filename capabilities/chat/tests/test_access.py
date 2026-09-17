"""Chat server authorization exercised against isolated, migrated Postgres."""
import asyncio

import pytest

from capabilities.chat.models import Access, Actor, ChatError
from capabilities.permissions.roles import ROLE_PERMISSIONS, FeatureSet, Role, invalidate_permissions_cache

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


async def group(team, **kwargs):
    return await team.service.create_group(team.actor('fleet'), title='Operations', kind='roles',
                                          role_keys=['fleet', 'dispatcher'], **kwargs)


async def advance(team, cid, seq):
    # Projection-only fixture: simulate an already committed message sequence.
    async with team.db.transaction():
        await team.db.chat_lock_account(team.account.id)
        await team.db._db.execute('UPDATE chat_conversations SET message_seq = ? WHERE account_id = ? AND id = ?',
                                  (seq, team.account.id, cid))


async def change_user(team, name, **changes):
    for field, value in changes.items():
        assert field in ('role', 'is_active', 'is_manager', 'is_primary_owner')
        await team.db._db.execute(f'UPDATE users SET {field} = ? WHERE account_id = ? AND id = ?',
                                  (value, team.account.id, team.users[name].id))


async def test_chat_is_explicitly_off_for_every_default_and_existing_row(team):
    assert FeatureSet().can_view_chat is False
    assert all(not fs.can_view_chat for fs in ROLE_PERMISSIONS.values())
    await team.db.set_role_permissions(team.account.id, 'owner', {'can_manage_users': True})
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.ensure_general(team.actor('owner'))


async def test_general_and_direct_are_unique_under_concurrent_requests(team):
    invalidate_permissions_cache(team.account.id)  # cold resolver + pool size 2
    a, b = await asyncio.wait_for(asyncio.gather(
        team.service.ensure_general(team.actor('fleet')),
        team.service.ensure_general(team.actor('owner')),
    ), timeout=10)
    assert a['id'] == b['id'] and a['owner_user_id'] == team.users['owner'].id
    a, b = await asyncio.wait_for(asyncio.gather(
        team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id),
        team.service.ensure_direct(team.actor('peer'), team.users['fleet'].id),
    ), timeout=10)
    assert a['id'] == b['id'] and a['owner_user_id'] is None
    with pytest.raises(ChatError, match='not_found'):
        await team.service.get_conversation(team.actor('owner'), a['id'])
    with pytest.raises(ChatError, match='invalid_participant'):
        await team.service.ensure_direct(team.actor('fleet'), team.users['fleet'].id)


async def test_audience_and_account_wall_even_for_primary_owner(team):
    c = await group(team)
    for name in ('owner', 'outsider'):
        with pytest.raises(ChatError, match='not_found'):
            await team.service.get_conversation(team.actor(name), c['id'])
    account = await team.db.create_account('Other account')
    user = await team.db.create_user(89000, account.id, role=Role.OWNER)
    await team.db.set_role_permissions(account.id, 'owner', {'can_view_chat': True})
    with pytest.raises(ChatError, match='not_found'):
        await team.service.get_conversation(Actor(account.id, user.id), c['id'])
    with pytest.raises(ChatError, match='not_found'):
        await team.service.ensure_direct(team.actor('owner'), user.id)
    assert await team.db.chat_get_conversation(account.id, c['id']) is None


async def test_history_stamped_at_team_change_not_first_open(team):
    c = await group(team, new_member_history='since_join')
    before = await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id)
    await advance(team, c['id'], 8)
    await change_user(team, 'peer', role='fleet')  # still eligible: same interval
    assert await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id) == before
    await change_user(team, 'peer', role='hr')
    assert await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id) is None
    await advance(team, c['id'], 12)
    await change_user(team, 'peer', role='dispatcher')
    await advance(team, c['id'], 15)  # user has not opened Chat between 12 and 15
    view = await team.service.get_conversation(team.actor('peer'), c['id'])
    assert view['caller']['visible_after_message_seq'] == 12
    access = Access('member', frozenset({'read'}), 12)
    assert not access.sees_message(12) and access.sees_message(13)
    after = await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id)
    assert after['id'] != before['id']


async def test_history_setting_affects_future_intervals_only(team):
    c = await group(team)
    await advance(team, c['id'], 9)
    c = await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'],
                                        changes={'new_member_history': 'since_join'})
    assert (await team.service.get_conversation(team.actor('peer'), c['id']))['caller']['visible_after_message_seq'] == 0
    await change_user(team, 'peer', is_active=0)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.get_conversation(team.actor('peer'), c['id'])
    await change_user(team, 'peer', is_active=1)
    assert (await team.service.get_conversation(team.actor('peer'), c['id']))['caller']['visible_after_message_seq'] == 9
    c = await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'],
                                        changes={'new_member_history': 'all'})
    await change_user(team, 'peer', is_active=0)
    await change_user(team, 'peer', is_active=1)
    assert (await team.service.get_conversation(team.actor('peer'), c['id']))['caller']['visible_after_message_seq'] == 0


async def test_grant_and_account_holds_do_not_rejoin(team):
    c = await group(team, new_member_history='since_join')
    before = await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id)
    await advance(team, c['id'], 20)
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.get_conversation(team.actor('peer'), c['id'])
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': True})
    invalidate_permissions_cache(team.account.id)
    await team.db._db.execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (team.account.id,))
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.get_conversation(team.actor('peer'), c['id'])
    await team.db._db.execute('UPDATE accounts SET is_active = 1 WHERE id = ?', (team.account.id,))
    assert (await team.service.get_conversation(team.actor('peer'), c['id']))['caller']['visible_after_message_seq'] == 0
    assert await team.db.chat_membership(team.account.id, c['id'], team.users['peer'].id) == before


async def test_delegation_is_action_specific_and_cannot_escalate(team):
    c = await group(team, posting_mode='admins')
    peer = team.actor('peer')
    assert 'send' not in (await team.service.get_conversation(peer, c['id']))['caller']['actions']
    c = await team.service.set_admin(team.actor('fleet'), c['id'], peer.user_id,
                                     expected_version=c['version'], actions=[])
    view = await team.service.get_conversation(peer, c['id'])
    assert view['caller']['group_role'] == 'admin' and 'send' in view['caller']['actions']
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.update_group(peer, c['id'], expected_version=c['version'], changes={'title': 'Changed'})
    c = await team.service.set_admin(team.actor('fleet'), c['id'], peer.user_id,
                                     expected_version=c['version'], actions=['manage_info'])
    c = await team.service.update_group(peer, c['id'], expected_version=c['version'], changes={'title': 'Changed'})
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.set_admin(peer, c['id'], team.users['outsider'].id,
                                    expected_version=c['version'], actions=['manage_settings'])
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.update_group(peer, c['id'], expected_version=c['version'],
                                        changes={'title': 'Sneaky', 'posting_mode': 'everyone'})
    assert (await team.service.get_conversation(peer, c['id']))['title'] == 'Changed'
    with pytest.raises(ChatError, match='invalid_admin_actions'):
        await team.service.set_admin(team.actor('fleet'), c['id'], peer.user_id,
                                     expected_version=c['version'], actions=['manage_admins'])
    await change_user(team, 'peer', role='hr')
    with pytest.raises(ChatError, match='not_found'):
        await team.service.get_conversation(peer, c['id'])


async def test_settings_version_and_general_archive_protection(team):
    c = await team.service.ensure_general(team.actor('owner'))
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.update_group(team.actor('owner'), c['id'], expected_version=c['version'], changes={'archived': True})
    c = await group(team)
    a, b = await asyncio.gather(*[
        team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'], changes={'title': title})
        for title in ('First', 'Second')
    ], return_exceptions=True)
    assert sum(isinstance(v, dict) for v in (a, b)) == 1
    assert [v.code for v in (a, b) if isinstance(v, ChatError)] == ['version_conflict']
    view = a if isinstance(a, dict) else b
    view = await team.service.update_group(team.actor('fleet'), c['id'], expected_version=view['version'], changes={'archived': True})
    assert 'send' not in view['caller']['actions']


async def test_transfer_removes_former_owner_privileges(team):
    c = await group(team)
    c = await team.service.set_admin(team.actor('fleet'), c['id'], team.users['peer'].id,
                                     expected_version=c['version'], actions=['manage_info'])
    with pytest.raises(ChatError, match='owner_ineligible'):
        await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'],
                                        changes={'role_keys': ['dispatcher']})
    c = await team.service.transfer_ownership(team.actor('fleet'), c['id'], team.users['peer'].id,
                                             expected_version=c['version'])
    assert c['caller']['group_role'] == 'member' and 'manage_admins' not in c['caller']['actions']
    assert await team.db.chat_admin_actions(team.account.id, c['id'], team.users['peer'].id) is None
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.transfer_ownership(team.actor('fleet'), c['id'], team.users['fleet'].id,
                                             expected_version=c['version'])


async def test_recovery_requires_primary_owner_and_structural_owner_loss(team):
    c = await group(team)
    with pytest.raises(ChatError, match='owner_still_eligible'):
        await team.service.transfer_ownership(team.actor('owner'), c['id'], team.users['peer'].id,
                                             expected_version=c['version'], recovery=True)
    await team.db.set_role_permissions(team.account.id, 'fleet', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='owner_still_eligible'):
        await team.service.transfer_ownership(team.actor('owner'), c['id'], team.users['peer'].id,
                                             expected_version=c['version'], recovery=True)
    await change_user(team, 'fleet', is_active=0)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.transfer_ownership(team.actor('outsider'), c['id'], team.users['peer'].id,
                                             expected_version=c['version'], recovery=True)
    receipt = await team.service.transfer_ownership(team.actor('owner'), c['id'], team.users['peer'].id,
                                                  expected_version=c['version'], recovery=True)
    assert set(receipt) == {'id', 'owner_user_id', 'version'}
    assert receipt['owner_user_id'] == team.users['peer'].id
    with pytest.raises(ChatError, match='not_found'):
        await team.service.get_conversation(team.actor('owner'), c['id'])
    cur = await team.db._db.execute("SELECT changes FROM activity_events WHERE account_id = ? AND action = 'chat_ownership_recovered'",
                                    (team.account.id,))
    assert str(team.users['peer'].id) in (await cur.fetchone())['changes']


async def test_current_tier_is_resolved_instead_of_base_role(team):
    await team.db.set_role_permissions(team.account.id, 'fleet__manager', {'can_view_chat': False})
    await change_user(team, 'fleet', is_manager=1)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.ensure_general(team.actor('fleet'))
    await change_user(team, 'owner', is_primary_owner=0)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.ensure_general(team.actor('owner'))


async def test_missing_interval_and_database_failure_deny_access(team, monkeypatch):
    c = await group(team)
    await team.db._db.execute('DELETE FROM chat_membership_intervals WHERE account_id = ? AND conversation_id = ? AND user_id = ?',
                              (team.account.id, c['id'], team.users['peer'].id))
    with pytest.raises(ChatError, match='not_found'):
        await team.service.get_conversation(team.actor('peer'), c['id'])
    async def unavailable(*args, **kwargs):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(team.db, 'chat_get_user', unavailable)
    with pytest.raises(RuntimeError, match='unavailable'):
        await team.service.get_conversation(team.actor('fleet'), c['id'])


async def test_metadata_audit_and_journal_rollback_together(team, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr(team.db, 'append_activity_events', unavailable)
    with pytest.raises(RuntimeError, match='unavailable'):
        await group(team)
    assert await team.db.chat_active_group_count(team.account.id) == 0
    cur = await team.db._db.execute('SELECT count(*) AS n FROM chat_membership_intervals WHERE account_id = ?', (team.account.id,))
    assert (await cur.fetchone())['n'] == 0


@pytest.mark.parametrize('changes', [{'title': '   '}, {'title': 'a' * 81}, {'description': 'x' * 301},
                                    {'posting_mode': 'anyone'}, {'new_member_history': 'guess'}, {'archived': 'true'}])
async def test_invalid_settings_do_not_mutate(team, changes):
    c = await group(team)
    with pytest.raises(ChatError):
        await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'], changes=changes)
    assert (await team.service.get_conversation(team.actor('fleet'), c['id']))['version'] == c['version']


async def test_plan_mask_can_close_chat_even_for_primary_owner(team):
    from capabilities.permissions import plans
    account = await team.db.get_account(team.account.id)
    await team.db.upsert_plan(account.tier, label='Without Chat', included=[key for key in plans.EXCLUDABLE if key != 'chat'])
    await plans.refresh_plans(team.db, force=True)
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.ensure_general(team.actor('owner'))
    assert (await team.db.get_role_permissions(team.account.id, 'owner'))['can_view_chat'] is True


async def test_peer_grant_revocation_disables_direct_send_but_keeps_authorized_history(team):
    c = await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    assert 'send' in c['caller']['actions']
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    c = await team.service.get_conversation(team.actor('fleet'), c['id'])
    assert 'read' in c['caller']['actions'] and 'send' not in c['caller']['actions']
    with pytest.raises(ChatError, match='not_found'):
        await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)


async def test_role_change_during_grant_resolution_cannot_reuse_old_grant(team, monkeypatch):
    import capabilities.chat.service as module
    original = module.resolve_actor
    async def change_after_resolve(db, actor):
        verified = await original(db, actor)
        await change_user(team, 'fleet', role='driver')  # Chat default off
        return verified
    monkeypatch.setattr(module, 'resolve_actor', change_after_resolve)
    with pytest.raises(ChatError, match='identity_changed'):
        await group(team)
    assert await team.db.chat_active_group_count(team.account.id) == 0


async def test_quota_includes_unarchive_but_excludes_general_and_direct(team, monkeypatch):
    import capabilities.chat.service as module
    monkeypatch.setattr(module, 'MAX_ACTIVE_GROUPS', 1)
    await team.service.ensure_general(team.actor('fleet'))
    await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    first = await group(team)
    with pytest.raises(ChatError, match='group_limit'):
        await group(team)
    first = await team.service.update_group(team.actor('fleet'), first['id'], expected_version=first['version'], changes={'archived': True})
    await group(team)
    with pytest.raises(ChatError, match='group_limit'):
        await team.service.update_group(team.actor('fleet'), first['id'], expected_version=first['version'], changes={'archived': False})


async def test_nfc_normalization_and_canonical_roles(team):
    c = await team.service.create_group(team.actor('fleet'), title='  Cafe\u0301  ')
    assert c['title'] == 'Café'
    for roles in (['dispatch'], ['fleet_manager'], ['hr'], []):
        with pytest.raises(ChatError, match='invalid_audience'):
            await team.service.create_group(team.actor('fleet'), title='Invalid', kind='roles', role_keys=roles)


async def test_audit_contains_no_private_group_text_or_dm_participant_list(team):
    c = await group(team)
    await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'],
                                    changes={'title': 'Private title', 'description': 'Private description'})
    dm = await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    cur = await team.db._db.execute('SELECT entity_id, changes, note, context FROM activity_events WHERE account_id = ?', (team.account.id,))
    rows = [dict(r) for r in await cur.fetchall()]
    assert rows and dm['id'] not in {r['entity_id'] for r in rows}
    assert 'Private title' not in str(rows) and 'Private description' not in str(rows)
