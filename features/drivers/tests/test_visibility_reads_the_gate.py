"""The driver-visibility wall reads the GATE's answer, not the role's.

``can(role, flag)`` is the role's built-in default — account-aware only
inside the bot, whose auth primes a contextvar.  In the API, every route
is gated, and the gate stashes the account-aware FeatureSet it resolved
(matrix overrides, manager tier, module masks) on ``user["_perms"]``.
The wall must read THAT: an owner who revokes Manage from a role whose
default is True must be honoured, and one who grants it to a role whose
default is False must be honoured too.  The review that found this: a
fleet member kept reading and editing every driver's profile (CDL, home
address) after the owner had unticked it.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
from fastapi import HTTPException

from capabilities.permissions.roles import FeatureSet
from features.drivers import router as R


class _Row:
    def __init__(self, id, account_id=1):
        self.id, self.account_id = id, account_id


class _DB:
    async def get_user_by_telegram_id(self, tg):
        return _Row(id=10)          # the caller is users.id 10
    async def get_user_by_id(self, uid):
        return _Row(id=uid)
    async def get_user_company_codes(self, uid):
        return []


def _caller(role: str, manage: bool | None):
    c = {"sub": "999", "role": role, "account_id": 1}
    if manage is not None:
        c["_perms"] = FeatureSet(can_manage_driver_docs=manage, can_view_driver_docs=True)
    return c


@pytest.fixture(autouse=True)
def _no_company_wall(monkeypatch):
    async def _codes(user):
        return []
    monkeypatch.setattr(R, "get_user_company_codes", _codes)


@pytest.mark.asyncio
class TestTheWallReadsTheGate:
    async def test_an_owner_grant_is_honoured_over_the_role_default(self):
        # A driver's built-in default is Manage=False and their person
        # width is 'self'; the account granted them Manage (a driver
        # coordinator) — the gate's answer, not the role's, opens the
        # account's rows.
        target = await R._require_driver_visibility(20, _caller("driver", manage=True), _DB())
        assert target.id == 20

    async def test_without_the_grant_a_driver_reads_only_themself(self):
        with pytest.raises(HTTPException) as e:
            await R._require_driver_visibility(20, _caller("driver", manage=False), _DB())
        assert e.value.status_code == 404

    def test_edits_read_the_gate_too(self):
        # update_driver's admin check is the same _holds: a fleet member
        # (built-in Manage=True) whose account revoked it is NOT an admin.
        assert R._holds(_caller("fleet", manage=False), "can_manage_driver_docs") is False
        assert R._holds(_caller("dispatcher", manage=True), "can_manage_driver_docs") is True

    async def test_self_is_always_reachable(self):
        target = await R._require_driver_visibility(10, _caller("driver", manage=False), _DB())
        assert target.id == 10

    async def test_a_caller_with_no_gate_stash_holds_nothing(self):
        # No _perms (an ungated path): the manage verb is NOT assumed from
        # the role — a self-width caller without the stash reads only
        # themself, whatever their role's default would say.
        with pytest.raises(HTTPException):
            await R._require_driver_visibility(20, _caller("driver", manage=None), _DB())
        assert R._holds(_caller("fleet", manage=None), "can_manage_driver_docs") is False

    async def test_the_wall_never_asks_the_role_default(self, monkeypatch):
        # Mutation guard: a bare can() anywhere on this path would call
        # roles.can — make that a loud failure.
        import capabilities.permissions.roles as roles
        def boom(*a, **k):
            raise AssertionError("bare can() on the API path")
        monkeypatch.setattr(roles, "can", boom)
        target = await R._require_driver_visibility(20, _caller("driver", manage=True), _DB())
        assert target.id == 20
