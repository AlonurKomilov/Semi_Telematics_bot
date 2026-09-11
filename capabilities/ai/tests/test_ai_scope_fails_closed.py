"""Guard: a restriction we cannot READ is not a restriction we may ignore.

Two reads decide how narrow an AI caller is, and both used to swallow
their failure into the value that means UNRESTRICTED:

* ``resolve_vehicle_scope`` collapsed a failed company-codes read into
  ``company_codes = []``, indistinguishable from a successful empty
  read, and returned ``None`` — the module's own "no restriction"
  sentinel. One pool timeout on that single query turned a dispatcher
  restricted to one company into an unrestricted one for that chat turn:
  every other company's vehicles, positions, drivers and work orders
  narrated to them as their own.
* ``_get_user_info`` repeated the swallow for the company channel, so
  the same outage also handed ``get_geofences`` every company's zones.

The sibling read 30 lines below the first one already failed CLOSED and
said so in a comment; these two are now consistent with it.

The cost is real and deliberate: during such a failure an unrestricted
member is denied rather than served. That is the direction this file
chooses on purpose.
"""

import pytest

from capabilities.ai.scope import resolve_vehicle_scope


_ACCT = iter(range(200000, 300000))


def _acct() -> int:
    """A fresh account id per test — the role-width layer caches per
    (account, role), so two tests sharing one id share the cache."""
    return next(_ACCT)


class _Db:
    """A platform db whose company-codes read raises, like a pool timeout."""

    def __init__(self, codes=None, raises=True):
        self._codes = codes or []
        self._raises = raises

    async def get_user_company_codes(self, _uid):
        if self._raises:
            raise RuntimeError("pool exhausted")
        return list(self._codes)

    async def get_role_vehicle_scope(self, account_id, role):
        return None

    async def get_user_vehicle_nums(self, _uid):
        return []


class _WideMember:
    """A WIDE non-driver member — the shape that reaches the company read."""
    role = "dispatcher"
    vehicle_scope = None

    def scope_with_role_default(self, role_scope):
        return "all"


@pytest.mark.asyncio
async def test_an_unreadable_company_restriction_fails_closed():
    scope = await resolve_vehicle_scope(
        _Db(raises=True), _acct(), 42, "dispatcher",
        db_user=_WideMember(),
    )
    assert scope == [], (
        "a failed company-codes read must produce the empty (deny-all) "
        "scope, never None — None is this module's UNRESTRICTED value, so "
        "the failure widened the caller instead of narrowing them"
    )


@pytest.mark.asyncio
async def test_a_member_with_no_company_rows_is_still_unrestricted():
    """The successful-empty read keeps its meaning — only the RAISE changed."""
    scope = await resolve_vehicle_scope(
        _Db(codes=[], raises=False), _acct(), 42, "dispatcher",
        db_user=_WideMember(),
    )
    assert scope is None, "no company rows means no company restriction"


def test_the_router_uses_a_deny_all_sentinel_for_unreadable_company_codes():
    """The geofence channel is the second half of the same outage.

    ``execute_tool`` injects the codes only when they are truthy and
    ``get_geofences`` filters only when it receives some, so ``None``
    there means "every company's zones". The sentinel is a code nothing
    matches, mirroring the vehicle_filter sentinel beside it.
    """
    import inspect

    from capabilities.ai import router as mod

    src = inspect.getsource(mod._get_user_info)
    assert "__no_access__" in src, (
        "an unreadable company-codes read must fall back to a sentinel "
        "that matches nothing, not to None"
    )
    assert "_codes = []" not in src, "the old swallow is back"
