"""The ledger has a keep-window, declared like every other dataset's."""

from __future__ import annotations

import pytest


def test_the_ledger_is_registered_by_the_system_bootstrap_with_a_ninety_day_window():
    """The retention hub does not discover the system layer by name — a
    name it must not know. The system layer registers its own rules at
    boot, and the guarantee is unchanged: the ledger prunes at 90 days.
    Order-independent: the registry keeps what an import declared, and
    install() is idempotent, so it holds whether this test or an earlier
    one on the worker did the importing."""
    from capabilities.data_lifecycle.retention.registry import resolve
    from system import bootstrap
    bootstrap.reset_for_tests()
    try:
        bootstrap.install()
        got = {r.target.key: r for r in resolve(scope="platform")}
    finally:
        bootstrap.reset_for_tests()
    assert "security.requests" in got, "system.bootstrap must import system.security.retention"
    r = got["security.requests"]
    assert r.keep_days == 90
    assert r.target.scope == "platform"
    assert any(n.feature == "security" for n in r.needs)


@pytest.mark.asyncio
async def test_the_prune_executor_delegates_to_storage():
    from system.security import retention

    class _DB:
        async def prune_security_requests(self, keep_days):
            self.called = keep_days
            return 7
    db = _DB()
    assert await retention._prune(db, None, 90) == 7
    assert db.called == 90
