"""The slots the system layer fills — and what they answer when it is
not there."""

import pytest

from infra import policy

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean():
    policy.reset()
    yield
    policy.reset()


async def test_with_no_system_layer_nobody_is_held():
    assert await policy.account_held(1) is False
    assert await policy.user_held(1) is False
    assert await policy.account_held(None) is False


async def test_an_installed_gate_is_what_callers_see():
    async def yes(_): return True
    policy.install(account=yes)
    assert await policy.account_held(7) is True
    assert await policy.user_held(7) is False, "the other slot keeps its default"


async def test_a_caller_that_imported_the_function_early_still_sees_the_install():
    """Dispatch happens at call time, so `from infra.policy import
    account_held` at module import does not freeze the default in."""
    from infra.policy import account_held
    async def yes(_): return True
    policy.install(account=yes)
    assert await account_held(1) is True


async def test_a_gate_that_raises_fails_open(caplog):
    async def boom(_): raise RuntimeError("watcher down")
    policy.install(account=boom, user=boom)
    with caplog.at_level("WARNING"):
        assert await policy.account_held(1) is False
        assert await policy.user_held(1) is False
    assert any("not held" in r.getMessage() for r in caplog.records)


async def test_reset_restores_the_defaults():
    async def yes(_): return True
    policy.install(account=yes, user=yes)
    policy.reset()
    assert await policy.account_held(1) is False and await policy.user_held(1) is False
