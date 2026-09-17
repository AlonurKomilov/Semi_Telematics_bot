"""End-to-end domain invariants: real messages, permissions and Postgres."""
import asyncio
from uuid import uuid4

import pytest

from capabilities.chat.models import ChatError
from capabilities.chat.service import ChatService
from capabilities.permissions.roles import Role, invalidate_permissions_cache

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


async def room(team, **kwargs):
    return await team.service.create_group(team.actor('fleet'), title='Private operations', kind='roles',
        role_keys=['fleet', 'dispatcher'], **kwargs)


async def send(team, cid, body='Message', *, who='fleet', key=None, **kwargs):
    return (await team.service.send_message(team.actor(who), cid, body=body, client_message_id=key or str(uuid4()), **kwargs))['message']


async def rows(team, table):
    assert table in ('chat_messages', 'chat_events', 'chat_event_deliveries', 'chat_mentions', 'chat_pins')
    cur = await team.db._db.execute(f'SELECT * FROM {table} WHERE account_id = ?', (team.account.id,))
    return [dict(r) for r in await cur.fetchall()]


async def test_concurrent_retry_saves_one_message_event_and_outbox_fanout(team):
    c = await room(team)
    responses = await asyncio.wait_for(asyncio.gather(*[
        team.service.send_message(team.actor('fleet'), c['id'], client_message_id='client-1', body='Cafe\u0301', mentions=[team.users['peer'].id])
        for _ in range(2)
    ]), 10)
    assert sorted(r['replayed'] for r in responses) == [False, True]
    assert len({r['message']['id'] for r in responses}) == 1
    assert responses[0]['message']['body'] == 'Café'
    assert len(await rows(team, 'chat_messages')) == 1
    events = [r for r in await rows(team, 'chat_events') if r['kind'] == 'message_created']
    assert len(events) == 1
    deliveries = [r for r in await rows(team, 'chat_event_deliveries') if r['event_id'] == events[0]['id']]
    assert {r['recipient_user_id'] for r in deliveries} == {team.users['fleet'].id, team.users['peer'].id}
    assert {r['event_id'] for r in deliveries} == {events[0]['id']}
    assert 'body' not in events[0] and 'body' not in deliveries[0]
    assert 'request_fingerprint' not in responses[0]['message']
    with pytest.raises(ChatError, match='idempotency_conflict'):
        await send(team, c['id'], body='Different', key='client-1')


async def test_retry_uses_original_fingerprint_after_edit_delete_and_service_recreation(team):
    c = await room(team)
    m = await send(team, c['id'], 'Original', key='retry')
    m = await team.service.edit_message(team.actor('fleet'), c['id'], m['id'], expected_version=1, body='Edited')
    team.service = ChatService(team.db)
    retry = await send(team, c['id'], 'Original', key='retry')
    assert retry['id'] == m['id'] and retry['body'] == 'Edited' and retry['version'] == 2
    await team.service.delete_message(team.actor('fleet'), c['id'], m['id'], expected_version=2)
    retry = await send(team, c['id'], 'Original', key='retry')
    assert retry['body'] is None and retry['deleted_at'] is not None and retry['version'] == 3
    assert (await team.db.chat_get_conversation(team.account.id, c['id']))['message_seq'] == 1
    assert len(await rows(team, 'chat_messages')) == 1


async def test_client_ids_are_scoped_to_author_and_conversation(team):
    c, d = await room(team), await room(team)
    first = await send(team, c['id'], key='same')
    second = await send(team, c['id'], key='same', who='peer')
    third = await send(team, d['id'], key='same')
    assert len({first['id'], second['id'], third['id']}) == 3
    peer_view = await team.service.get_message(team.actor('peer'), c['id'], first['id'])
    assert 'client_message_id' not in peer_view


async def test_failed_outbox_rolls_back_message_mentions_events_and_sequence(team, monkeypatch):
    c = await room(team)
    original_deliveries = await rows(team, 'chat_event_deliveries')
    original = team.db.chat_record_message_event
    async def fail_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('outbox failure')
    monkeypatch.setattr(team.db, 'chat_record_message_event', fail_after_insert)
    with pytest.raises(RuntimeError, match='outbox failure'):
        await send(team, c['id'], mentions=[team.users['peer'].id])
    for table in ('chat_messages', 'chat_mentions'):
        assert await rows(team, table) == []
    assert await rows(team, 'chat_event_deliveries') == original_deliveries
    assert len(await rows(team, 'chat_events')) == 1  # only group creation
    assert (await team.db.chat_get_conversation(team.account.id, c['id']))['message_seq'] == 0
    monkeypatch.setattr(team.db, 'chat_record_message_event', original)
    assert (await send(team, c['id']))['message_seq'] == 1


async def test_history_boundary_filters_every_content_surface_and_old_retries(team):
    c = await room(team, new_member_history='since_join')
    old = await send(team, c['id'], 'SECRET old')
    owned = await send(team, c['id'], 'SECRET own old', who='peer', key='old-own')
    await team.service.pin_message(team.actor('fleet'), c['id'], old['id'], expected_version=1)
    await team.service.draft(team.actor('peer'), c['id'], body='My draft', reply_to_id=old['id'], expected_version=0)
    await team.db.update_user(team.users['peer'].id, role='hr')
    await send(team, c['id'], 'SECRET during absence')
    await team.db.update_user(team.users['peer'].id, role='dispatcher')
    newest = await send(team, c['id'], 'New visible', reply_to_id=old['id'])
    actor = team.actor('peer')
    page = await team.service.list_messages(actor, c['id'], limit=1)
    assert [m['id'] for m in page['items']] == [newest['id']] and page['next_before'] is None
    assert page['items'][0]['reply'] == {'status': 'unavailable'}
    assert (await team.service.list_messages(actor, c['id'], query='SECRET'))['items'] == []
    assert (await team.service.list_messages(actor, c['id'], pinned=True))['items'] == []
    assert (await team.service.draft(actor, c['id']))['reply'] == {'status': 'unavailable'}
    summary = next(x for x in (await team.service.bootstrap(actor))['items'] if x['id'] == c['id'])
    assert summary['unread_count'] == 1 and summary['latest_message']['reply'] == {'status': 'unavailable'}
    for mid in (old['id'], owned['id']):
        with pytest.raises(ChatError, match='not_found'):
            await team.service.get_message(actor, c['id'], mid)
    with pytest.raises(ChatError, match='not_found'):
        await send(team, c['id'], 'SECRET own old', who='peer', key='old-own')
    with pytest.raises(ChatError, match='not_found'):
        await send(team, c['id'], who='peer', reply_to_id=old['id'])


async def test_descending_pagination_search_and_literal_wildcards(team):
    c = await room(team)
    messages = [await send(team, c['id'], body) for body in ('50% ready', 'other', '50% shipped', '50X done', '50% received')]
    first = await team.service.list_messages(team.actor('peer'), c['id'], query='50%', limit=2)
    second = await team.service.list_messages(team.actor('peer'), c['id'], query='50%', limit=2, before=first['next_before'])
    assert [r['id'] for r in first['items'] + second['items']] == [messages[i]['id'] for i in (4, 2, 0)]
    assert second['next_before'] is None
    first = await team.service.list_messages(team.actor('peer'), c['id'], limit=2)
    await send(team, c['id'], 'Later arrival')
    second = await team.service.list_messages(team.actor('peer'), c['id'], before=first['next_before'], limit=10)
    assert [r['message_seq'] for r in first['items'] + second['items']] == [5, 4, 3, 2, 1]


async def test_moderation_tombstone_clears_all_old_content_references(team):
    c = await room(team)
    m = await send(team, c['id'], 'Sensitive body', who='peer', mentions=[team.users['fleet'].id])
    reply = await send(team, c['id'], 'Reply', reply_to_id=m['id'])
    await team.service.pin_message(team.actor('fleet'), c['id'], m['id'], expected_version=1)
    await team.service.draft(team.actor('peer'), c['id'], expected_version=0, body='draft', reply_to_id=m['id'])
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.edit_message(team.actor('fleet'), c['id'], m['id'], expected_version=1, body='Owner rewrite')
    with pytest.raises(ChatError, match='forbidden'):
        await team.service.delete_message(team.actor('peer'), c['id'], reply['id'], expected_version=1)
    deleted = await team.service.delete_message(team.actor('fleet'), c['id'], m['id'], expected_version=1)
    assert deleted['body'] is None and deleted['mentions'] == [] and deleted['reply'] is None
    assert (await team.service.get_message(team.actor('peer'), c['id'], reply['id']))['reply'] == {'status': 'unavailable'}
    assert (await team.service.draft(team.actor('peer'), c['id']))['reply'] == {'status': 'unavailable'}
    assert (await team.service.list_messages(team.actor('peer'), c['id'], query='Sensitive'))['items'] == []
    assert (await team.service.list_messages(team.actor('peer'), c['id'], pinned=True))['items'] == []
    assert await rows(team, 'chat_mentions') == [] and await rows(team, 'chat_pins') == []
    stored = await team.db.chat_message(team.account.id, c['id'], m['id'])
    assert stored['body'] is None and stored['reply_to_id'] is None


async def test_edits_use_optimistic_versions_under_concurrency(team):
    c = await room(team)
    m = await send(team, c['id'])
    result = await asyncio.gather(*[
        team.service.edit_message(team.actor('fleet'), c['id'], m['id'], expected_version=1, body=body)
        for body in ('One', 'Two')
    ], return_exceptions=True)
    assert sum(isinstance(r, dict) for r in result) == 1
    assert [r.code for r in result if isinstance(r, ChatError)] == ['version_conflict']
    assert len([r for r in await rows(team, 'chat_events') if r['kind'] == 'message_edited']) == 1


async def test_mentions_require_live_same_account_grant_and_audience(team):
    c = await room(team)
    other = await team.db.create_account('Another account')
    foreign = await team.db.create_user(99551, other.id, role=Role.OWNER)
    for target in (foreign.id, team.users['outsider'].id):
        with pytest.raises(ChatError, match='invalid_mention'):
            await send(team, c['id'], mentions=[target])
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='invalid_mention'):
        await send(team, c['id'], mentions=[team.users['peer'].id])
    assert await rows(team, 'chat_messages') == []


async def test_read_cursor_is_monotonic_observed_bounded_and_excludes_own_deleted(team):
    c = await room(team)
    m = await send(team, c['id'])
    actor = team.actor('peer')
    with pytest.raises(ChatError, match='unseen_cursor'):
        await team.service.read_messages(actor, c['id'], through_message_seq=1)
    await team.service.list_messages(actor, c['id'])
    own = await send(team, c['id'], who='peer')
    state = await team.service.read_messages(actor, c['id'], through_message_seq=own['message_seq'])
    assert state['last_read_message_seq'] == 2
    state = await team.service.read_messages(actor, c['id'], through_message_seq=1)
    assert state['last_read_message_seq'] == 2
    with pytest.raises(ChatError, match='unseen_cursor'):
        await team.service.read_messages(actor, c['id'], through_message_seq=3)
    newer = await send(team, c['id'])
    await team.service.delete_message(team.actor('fleet'), c['id'], newer['id'], expected_version=1)
    summary = next(x for x in (await team.service.bootstrap(actor))['items'] if x['id'] == c['id'])
    assert summary['unread_count'] == 0
    assert (await team.service.get_message(actor, c['id'], m['id']))['body'] == 'Message'


async def test_private_drafts_state_and_clear_preserve_version(team):
    c = await room(team)
    actor = team.actor('peer')
    replies = await asyncio.gather(*[
        team.service.draft(actor, c['id'], expected_version=0, body=body)
        for body in ('One', 'Two')
    ], return_exceptions=True)
    assert sum(isinstance(r, dict) for r in replies) == 1
    assert [r.code for r in replies if isinstance(r, ChatError)] == ['version_conflict']
    assert (await team.service.draft(team.actor('fleet'), c['id'])) == {'body': '', 'version': 0, 'reply': None}
    cleared = await team.service.draft(actor, c['id'], expected_version=1, body='')
    assert cleared['version'] == 2 and cleared['body'] == ''
    with pytest.raises(ChatError, match='version_conflict'):
        await team.service.draft(actor, c['id'], expected_version=0, body='Stale resurrect')
    await team.service.personal_state(actor, c['id'], changes={'muted': True, 'pinned': True})
    assert not (await team.service.personal_state(team.actor('fleet'), c['id']))['muted']
    assert (await team.service.personal_state(actor, c['id']))['muted']
    with pytest.raises(ChatError, match='invalid_settings'):
        await team.service.personal_state(actor, c['id'], changes={'user_id': team.users['fleet'].id})


async def test_posting_archive_and_dm_peer_grant_apply_to_real_writes(team):
    c = await room(team, posting_mode='admins')
    with pytest.raises(ChatError, match='forbidden'):
        await send(team, c['id'], who='peer')
    c = await team.service.set_admin(team.actor('fleet'), c['id'], team.users['peer'].id, expected_version=c['version'], actions=[])
    await send(team, c['id'], who='peer')
    c = await team.service.update_group(team.actor('fleet'), c['id'], expected_version=c['version'], changes={'archived': True})
    with pytest.raises(ChatError, match='forbidden'):
        await send(team, c['id'])
    assert len((await team.service.list_messages(team.actor('peer'), c['id']))['items']) == 1
    dm = await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    await send(team, dm['id'])
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    with pytest.raises(ChatError, match='forbidden'):
        await send(team, dm['id'])
    assert len((await team.service.list_messages(team.actor('fleet'), dm['id']))['items']) == 1


async def test_people_picker_filters_grants_audience_and_has_no_private_profile_fields(team):
    c = await room(team)
    picker = await team.service.people(team.actor('fleet'), conversation_id=c['id'])
    assert {r['id'] for r in picker['items']} == {team.users['fleet'].id, team.users['peer'].id}
    assert all(set(r) == {'id', 'display_name', 'role'} for r in picker['items'])
    first = await team.service.people(team.actor('fleet'), limit=1)
    second = await team.service.people(team.actor('fleet'), limit=1, after=first['next_after'])
    assert first['items'][0]['id'] != second['items'][0]['id']
    await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
    invalidate_permissions_cache(team.account.id)
    assert [r['id'] for r in (await team.service.people(team.actor('fleet'), query='peer'))['items']] == []
    with pytest.raises(ChatError, match='not_found'):
        await team.service.people(team.actor('owner'), conversation_id=c['id'])


async def test_bootstrap_and_recovery_never_reveal_foreign_group_or_dm_content(team):
    c = await room(team)
    await send(team, c['id'], 'Private group content')
    dm = await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    await send(team, dm['id'], 'Private DM content')
    view = await team.service.bootstrap(team.actor('owner'))
    assert not {c['id'], dm['id']} & {r['id'] for r in view['items']}
    assert 'Private' not in str(view)
    assert (await team.service.recovery_groups(team.actor('owner')))['items'] == []
    await team.db.update_user(team.users['fleet'].id, is_active=False)
    recover = await team.service.recovery_groups(team.actor('owner'))
    assert recover['items'] == [{'id': c['id'], 'owner_user_id': team.users['fleet'].id, 'version': c['version']}]
    assert 'Private' not in str(recover)


@pytest.mark.parametrize('body', [' ', 'x' * 4001, 'bad\x00text'])
async def test_invalid_message_never_allocates_sequence(team, body):
    c = await room(team)
    with pytest.raises(ChatError, match='invalid_text'):
        await send(team, c['id'], body)
    assert (await team.db.chat_get_conversation(team.account.id, c['id']))['message_seq'] == 0


async def test_new_message_migration_preserves_fingerprint_and_refuses_unknown_legacy_payload(team):
    from adapters.storage.chat_schema import migrate_chat_message_api
    c = await room(team)
    m = await send(team, c['id'], 'Original', key='stable')
    before = (await team.db.chat_message(team.account.id, c['id'], m['id']))['request_fingerprint']
    await migrate_chat_message_api(team.db._db)
    await migrate_chat_message_api(team.db._db)
    assert (await team.db.chat_message(team.account.id, c['id'], m['id']))['request_fingerprint'] == before
    await team.db._db.execute('UPDATE chat_messages SET request_fingerprint = NULL WHERE account_id = ? AND id = ?', (team.account.id, m['id']))
    with pytest.raises(ChatError, match='idempotency_conflict'):
        await send(team, c['id'], 'Original', key='stable')


async def test_retry_still_conflicts_on_reply_or_mention_changes(team):
    c = await room(team)
    quoted = await send(team, c['id'], 'Quoted')
    await send(team, c['id'], 'Content', key='original', reply_to_id=quoted['id'], mentions=[team.users['peer'].id])
    for patch in ({'reply_to_id': None, 'mentions': [team.users['peer'].id]}, {'reply_to_id': quoted['id'], 'mentions': []}):
        with pytest.raises(ChatError, match='idempotency_conflict'):
            await send(team, c['id'], 'Content', key='original', **patch)


async def test_concurrent_distinct_messages_keep_sequences_in_commit_order(team):
    c = await room(team)
    messages = await asyncio.wait_for(asyncio.gather(send(team, c['id'], 'One'), send(team, c['id'], 'Two')), 10)
    assert sorted(m['message_seq'] for m in messages) == [1, 2]
    events = sorted((r for r in await rows(team, 'chat_events') if r['kind'] == 'message_created'), key=lambda r: r['event_seq'])
    by_id = {m['id']: m['message_seq'] for m in messages}
    assert [by_id[e['message_id']] for e in events] == [1, 2]


async def test_removed_member_cannot_use_messages_drafts_state_or_search(team):
    c = await room(team)
    m = await send(team, c['id'])
    await team.db.update_user(team.users['peer'].id, role='hr')
    operations = [
        lambda: team.service.list_messages(team.actor('peer'), c['id']),
        lambda: team.service.list_messages(team.actor('peer'), c['id'], query='Message'),
        lambda: team.service.get_message(team.actor('peer'), c['id'], m['id']),
        lambda: team.service.draft(team.actor('peer'), c['id']),
        lambda: team.service.personal_state(team.actor('peer'), c['id']),
        lambda: send(team, c['id'], who='peer'),
    ]
    for operation in operations:
        with pytest.raises(ChatError, match='not_found'):
            await operation()


async def test_edit_mentions_and_picker_respect_targets_join_history(team):
    c = await room(team, new_member_history='since_join')
    old = await send(team, c['id'], 'Before rejoin')
    await team.db.update_user(team.users['peer'].id, is_active=False)
    await team.db.update_user(team.users['peer'].id, is_active=True)
    with pytest.raises(ChatError, match='invalid_mention'):
        await team.service.edit_message(team.actor('fleet'), c['id'], old['id'], expected_version=1,
                                       body='Edited with mention', mentions=[team.users['peer'].id])
    targets = await team.service.people(team.actor('fleet'), conversation_id=c['id'], message_id=old['id'])
    assert [r['id'] for r in targets['items']] == [team.users['fleet'].id]
    assert (await team.service.get_message(team.actor('fleet'), c['id'], old['id']))['version'] == 1
    # The same person is eligible for a newly committed message.
    new = await send(team, c['id'], mentions=[team.users['peer'].id])
    assert new['mentions'] == [team.users['peer'].id]


async def test_bootstrap_mention_counts_and_draft_presence_are_caller_private(team):
    c = await room(team)
    mentioned = await send(team, c['id'], 'For peer', mentions=[team.users['peer'].id])
    await send(team, c['id'], 'Ordinary message')
    await team.service.draft(team.actor('peer'), c['id'], body='Private unfinished work', expected_version=0)

    async def summary(who):
        return next(r for r in (await team.service.bootstrap(team.actor(who)))['items'] if r['id'] == c['id'])

    peer = await summary('peer')
    assert (peer['unread_count'], peer['unread_mentions'], peer['has_draft']) == (2, 1, True)
    owner = await summary('fleet')
    assert (owner['unread_count'], owner['unread_mentions'], owner['has_draft']) == (0, 0, False)
    assert 'Private unfinished work' not in str(owner)
    await team.service.read_messages(team.actor('peer'), c['id'], through_message_seq=mentioned['message_seq'])
    assert (await summary('peer'))['unread_mentions'] == 0
    await team.service.draft(team.actor('peer'), c['id'], body='', expected_version=1)
    assert (await summary('peer'))['has_draft'] is False
