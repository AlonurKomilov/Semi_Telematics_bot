"""Shared isolated-Postgres Chat actors, with explicit test-only grants."""
from types import SimpleNamespace
import pytest_asyncio
from capabilities.chat.models import Actor
from capabilities.chat.service import ChatService
from capabilities.permissions.roles import Role, invalidate_permissions_cache


@pytest_asyncio.fixture
async def team(core_platform):
    db = core_platform
    account = await db.create_account('Chat test')
    users = {}
    for index, (name, role) in enumerate((('owner', Role.OWNER), ('fleet', Role.FLEET),
                                        ('peer', Role.DISPATCHER), ('outsider', Role.HR)), 1):
        users[name] = await db.create_user(88000 + index, account.id, role=role, display_name=name)
        await db.set_role_permissions(account.id, role.value, {'can_view_chat': True})
    invalidate_permissions_cache(account.id)
    return SimpleNamespace(db=db, account=account, service=ChatService(db), users=users,
                           actor=lambda name: Actor(account.id, users[name].id))
