"""Shared machinery for security tests — fixtures only, by design.

Sits with ``_repo.py`` and ``_swallow.py``: the underscore says this is
machinery the suite imports, never a file pytest collects.

**Fixtures, not assertions.** The obvious next step is a shelf of
``assert_cross_account_denied`` helpers, and it is the wrong one until
three packages have written the test by hand: an assertion shaped by the
first adopter misfits the second, and a helper nobody fits around is
worse than the four lines it replaced. What IS duplicated today is the
*setup* — every security test that drives a real request repeats the
same fifteen lines of platform swap, token minting and transport wiring
(``features/loads/tests/test_loads_write_scope.py`` is the clearest
copy). That is what lives here.

What a package's own security test is FOR: the behaviour the route guard
cannot see. ``interfaces/api/tests/test_every_route_is_gated.py`` proves
every route carries a gate; it cannot prove the handler behind the gate
filters its rows by account. Row-level security is enabled on one table
of forty-one, so that filtering is discipline, not structure — and
discipline is what a test is for.
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator

import pytest_asyncio


@contextlib.asynccontextmanager
async def api_client(db, monkeypatch) -> AsyncIterator[Any]:
    """The app, wired to ``db``, with an HTTP client in front of it.

    Swaps ``infra.platform._db`` for the test database before the app is
    built — the order matters, because routers resolve the platform
    handle at import — and yields an ``httpx`` client speaking ASGI
    directly, so no socket is opened and no port is bound.
    """
    import infra.platform as platform
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(platform, "_db", db, raising=False)
    from interfaces.api.app import create_api

    app = create_api()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        client.app = app          # so a test can override a dependency
        yield client


def bearer(user_id: int, account_id: int, role: str = "owner",
           *, telegram_id: int = 100_001, **claims) -> dict[str, str]:
    """An Authorization header for a real, signed token.

    A real token rather than a dependency override on purpose: the thing
    under test is usually the gate itself, and a test that overrides the
    gate to test the gate proves nothing.
    """
    from interfaces.api.auth import create_jwt

    token = create_jwt(telegram_id, account_id, role, user_id=user_id, **claims)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def two_accounts(db):
    """Two accounts with one owner each — the shape every isolation test
    needs, and the one thing worth building once.

    Returns ``{"a": {...}, "b": {...}}``, each with ``account``, ``user``
    and ``headers``. The question these exist to ask: does A's token
    reach B's rows?
    """
    from adapters.storage import Role

    out = {}
    for key, name in (("a", "Account A"), ("b", "Account B")):
        account = await db.create_account(name)
        user = await db.create_user(
            telegram_id=900_000 + len(out), account_id=account.id,
            role=Role.OWNER,
        )
        out[key] = {
            "account": account,
            "user": user,
            "headers": bearer(user.id, account.id, "owner",
                              telegram_id=user.telegram_id),
        }
    return out
