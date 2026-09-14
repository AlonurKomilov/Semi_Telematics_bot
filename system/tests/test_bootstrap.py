"""Installing the system layer changes what the customer layers are
told — and not installing it leaves them alone."""

import pytest

from infra import policy
from system import bootstrap

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean():
    bootstrap.reset_for_tests()
    yield
    bootstrap.reset_for_tests()


async def test_before_install_the_customer_layers_hold_nobody(seeded_db, monkeypatch):
    db = seeded_db["db"]; acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    await db.update_account(acct.id, security="quarantined")
    assert await policy.account_held(acct.id) is False, "no system layer, no hold"


async def test_after_install_a_held_account_is_held(seeded_db, monkeypatch):
    db = seeded_db["db"]; acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    from system.security import quarantine
    quarantine.forget_account()
    await db.update_account(acct.id, security="quarantined")
    bootstrap.install()
    assert await policy.account_held(acct.id) is True
    person = (await db.list_account_users(acct.id))[0]
    assert await policy.user_held(person.id) is False, "the person was never accused"


async def test_the_switch_is_folded_in_so_no_customer_layer_spells_it(seeded_db, monkeypatch):
    db = seeded_db["db"]; acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")
    from system.security import quarantine
    quarantine.forget_account()
    await db.update_account(acct.id, security="quarantined")
    bootstrap.install()
    assert await policy.account_held(acct.id) is False


def test_install_is_idempotent():
    bootstrap.install(); bootstrap.install()
    assert bootstrap._installed is True


def test_installing_imports_the_ledgers_retention_module():
    """Importing ``system.security.retention`` IS registering it — the
    module calls register_target/register_need at import. Whether that
    registration is correct is that module's own test; what bootstrap
    owes is the import, in a process that would otherwise never touch
    the system layer. (The registry is snapshot/restored between tests
    and the import is cached per process, so asserting on the registry's
    contents here would depend on test order.)"""
    import sys
    bootstrap.install()
    assert "system.security.retention" in sys.modules
    assert "system.capacity.retention" in sys.modules, (
        "a process that forgets this never prunes the capacity metrics")
    from system.security.retention import TARGET_KEY
    assert TARGET_KEY.startswith("security"), TARGET_KEY


def test_both_entrypoints_install_the_system_layer():
    """An entrypoint that forgets the install runs the customer layers
    with their defaults: nobody is held, and the ledger never prunes."""
    from tests._repo import REPO
    for entry in ("run.py", "interfaces/api/app.py"):
        src = (REPO / entry).read_text()
        assert "from system import bootstrap" in src and ".install()" in src, (
            f"{entry} does not install the system layer"
        )
