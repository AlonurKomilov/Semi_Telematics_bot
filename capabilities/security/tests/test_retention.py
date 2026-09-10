"""The ledger has a keep-window, declared like every other dataset's."""

from __future__ import annotations

import pytest


def test_the_ledger_is_discovered_with_a_ninety_day_window():
    from capabilities.data_lifecycle.retention import discover
    from capabilities.data_lifecycle.retention.registry import resolve
    discover()
    got = {r.target.key: r for r in resolve(scope="platform")}
    assert "security.requests" in got, "retention.__init__ must list capabilities.security.retention"
    r = got["security.requests"]
    assert r.keep_days == 90
    assert r.target.scope == "platform"
    assert any(n.feature == "security" for n in r.needs)


@pytest.mark.asyncio
async def test_the_prune_executor_delegates_to_storage():
    from capabilities.security import retention

    class _DB:
        async def prune_security_requests(self, keep_days):
            self.called = keep_days
            return 7
    db = _DB()
    assert await retention._prune(db, None, 90) == 7
    assert db.called == 90
