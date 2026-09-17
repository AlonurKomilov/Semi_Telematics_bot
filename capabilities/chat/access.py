"""Team/user identity and effective permissions remain the authorities.

Resolve grants before opening a Chat write transaction (the shared resolver
uses a separate read connection). Recheck role/tier under the account lock;
a changed identity returns conflict rather than reusing another role's grant.
Permission revocations retain the shared resolver's 60s cross-worker bound.
"""
from capabilities.permissions.roles import Role, get_user_permissions
from .models import ADMIN_ACTIONS, OWNER_ACTIONS, Access, Actor, ChatError

IDENTITY_FIELDS = ('id', 'account_id', 'role', 'is_manager', 'is_primary_owner')


def structurally_eligible(user: dict | None, conversation: dict) -> bool:
    if not user or not user['is_active'] or user['account_id'] != conversation['account_id']:
        return False
    if conversation['kind'] == 'account':
        return True
    if conversation['kind'] == 'roles':
        return user['role'] in conversation['role_keys']
    return user['id'] in (conversation['direct_low_user_id'], conversation['direct_high_user_id'])


async def resolve_actor(db, actor: Actor) -> dict:
    user = await db.chat_get_user(actor.account_id, actor.user_id)
    if not user or not user['is_active'] or not user['account_active']:
        raise ChatError('forbidden')
    try:
        role = Role(user['role'])
    except ValueError:
        raise ChatError('forbidden') from None
    perms = await get_user_permissions(
        role, actor.account_id, is_manager=bool(user['is_manager']),
        is_primary_owner=bool(user['is_primary_owner']),
    )
    if not perms.can_view_chat:
        raise ChatError('forbidden')
    return user


async def recheck_actor(db, expected: dict) -> dict:
    user = await db.chat_get_user(expected['account_id'], expected['id'])
    if not user or not user['is_active'] or not user['account_active']:
        raise ChatError('forbidden')
    if any(user[k] != expected[k] for k in IDENTITY_FIELDS):
        raise ChatError('identity_changed')
    return user


async def conversation_access(db, user: dict, conversation: dict | None) -> Access:
    """Requires a freshly rechecked, Chat-granted actor in a locked transaction."""
    if conversation is None or not structurally_eligible(user, conversation):
        raise ChatError('not_found')
    membership = await db.chat_membership(user['account_id'], conversation['id'], user['id'])
    # Missing projection is not evidence of an all-history join.
    if membership is None:
        raise ChatError('not_found')
    actions = {'read', 'personal_state'}
    group_role = 'member'
    if conversation['kind'] == 'direct':
        peer_id = (conversation['direct_high_user_id'] if user['id'] == conversation['direct_low_user_id']
                   else conversation['direct_low_user_id'])
        peer = await db.chat_get_user(user['account_id'], peer_id)
        if peer and peer['is_active'] and user.get('_chat_peer_granted', False):
            actions.add('send')
    else:
        admin = await db.chat_admin_actions(user['account_id'], conversation['id'], user['id'])
        if conversation['owner_user_id'] == user['id']:
            group_role = 'owner'
            actions.update(OWNER_ACTIONS)
        elif admin is not None:
            group_role = 'admin'
            actions.update(set(admin) & ADMIN_ACTIONS)
        if not conversation['archived'] and (conversation['posting_mode'] == 'everyone' or group_role != 'member'):
            actions.add('send')
        if conversation['system_key'] == 'general':
            actions.discard('archive_group')
    return Access(group_role, frozenset(actions), membership['visible_after_message_seq'])


def require_action(access: Access, action: str) -> None:
    if action not in access.actions:
        raise ChatError('forbidden')
