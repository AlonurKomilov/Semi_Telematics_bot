"""Policies the system layer hands DOWN at boot — closures, not imports.

The customer layers must never import ``system``: the system layer
watches and, when it must, holds them, and a layer that knows its
watcher's module names cannot later run without it on a separate
machine.  But those layers still have to ASK the questions the system
layer answers — "is this account held?", "is this person held?" — at
the moment a public recruiting link resolves, or a notification picks
its recipients.

So the questions live here, in the layer everyone may import, as
slots.  Each slot holds the customer layers' own answer when no system
layer is present — nobody is held — and the system layer replaces it
at boot (``system.bootstrap.install``).  A process that never installs
the system layer keeps the defaults, which is exactly what a customer
process on its own machine should do.

Every slot FAILS OPEN: a gate that raises answers "not held" and logs.
A fault in the watcher must not lock the customers out — a hold that
is not in force is a gap an operator can see, an outage is not.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Gate = Callable[[int | None], Awaitable[bool]]


async def _never(_subject_id: int | None) -> bool:
    """The default answer with no system layer installed."""
    return False


_account_gate: Gate = _never
_user_gate: Gate = _never


async def account_held(account_id: int | None) -> bool:
    """Whether this whole company is held.  Dispatches through the slot
    at CALL time, so a caller may import this function directly and
    still see what the system layer installed later."""
    try:
        return bool(await _account_gate(account_id))
    except Exception:
        logger.warning("policy: account gate failed for %s — answering "
                       "not held", account_id, exc_info=True)
        return False


async def user_held(user_id: int | None) -> bool:
    """Whether this person is held.  Same contract as ``account_held``."""
    try:
        return bool(await _user_gate(user_id))
    except Exception:
        logger.warning("policy: user gate failed for %s — answering "
                       "not held", user_id, exc_info=True)
        return False


def install(*, account: Gate | None = None, user: Gate | None = None) -> None:
    """Replace a slot.  Called once per process by the system layer's
    bootstrap; a slot left None keeps what it has."""
    global _account_gate, _user_gate
    if account is not None:
        _account_gate = account
    if user is not None:
        _user_gate = user


def reset() -> None:
    """Back to the defaults — for tests, and for nothing else."""
    global _account_gate, _user_gate
    _account_gate = _never
    _user_gate = _never
