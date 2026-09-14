"""What the system layer installs into a process at boot.

Called once from each entrypoint that runs with the system layer
present — the API's lifespan and the bot's ``main`` — and from nowhere
below ``interfaces/``: an entrypoint may import ``system``; the layers
it wires may not.  A process that never calls this runs the customer
layers with their own defaults (nobody is held, nothing is pruned on
the system layer's behalf), which is what a customer-only process on
its own machine should do.

Two things are installed:

* the hold questions, as closures into ``infra.policy`` — each folds in
  the enforcement switch, so no customer layer ever spells the switch's
  name;
* the security ledger's retention rules, by importing the module whose
  import registers them — the retention hub discovers contributors by
  name, and this is the one name it must not know.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    from infra import policy
    from system.security import quarantine

    async def account_held(account_id: int | None) -> bool:
        if not quarantine.enabled():
            return False
        return await quarantine.is_account_held(account_id)

    async def user_held(user_id: int | None) -> bool:
        if not quarantine.enabled():
            return False
        return await quarantine.is_held(user_id)

    policy.install(account=account_held, user=user_held)

    # Importing it IS registering it: the module calls register_target /
    # register_need at import time.
    import system.security.retention  # noqa: F401
    import system.capacity.retention  # noqa: F401

    _installed = True
    logger.info("system layer installed: hold policies + ledger retention")


def reset_for_tests() -> None:
    global _installed
    _installed = False
    from infra import policy
    policy.reset()
