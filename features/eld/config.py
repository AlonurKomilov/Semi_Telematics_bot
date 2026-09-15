"""ELD config — which device wins when a driver is on two of them.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  This is the ELD feature's own, and it is deliberately NOT
part of ``/vehicles/config``: hours of service is its own domain with its
own permissions and its own page, and routing an ELD setting through the
Vehicles feature would put one feature's config in another's URL.  The
chain a reader should be able to follow is source → eld → ``/eld/config``,
exactly as it is source → vehicles → ``/vehicles/config`` next door.

⚠️  MOUNT THIS ROUTER BEFORE ``features.eld.router`` — the same
registration-order rule ``features/vehicles/config.py`` carries.

THE UNIT IS THE WHOLE READING, NOT THE FIELD
--------------------------------------------
The vehicle entity next door arbitrates field by field, and that is right
for it: a VIN from one integration and a plate from another are
independent facts about a static object, and taking the best of each
loses nothing.

Hours of service is the opposite. A duty status and its four clocks are
ONE coherent observation, made by ONE certified device, at ONE instant.
Taking the status from device A and the drive clock from device B
produces a reading that neither device ever reported and that no ELD
would sign — on the one surface in this product that must never invent a
state. So the entity declares a single pseudo-field, ``reading``, and the
owner's choice moves whole readings.

WHEN IT APPLIES
---------------
Almost never, and that is the point: two ELDs normally report DIFFERENT
drivers, and that union needs no arbitration. This exists for the
overlap — a driver logged on both devices mid-migration — where two
certified devices are each authoritative about the same person's hours.

Only LINKED drivers can overlap. An unlinked row is a person we have not
matched to our roster, so we cannot know that two of them are the same
human and must not guess.

WHY THERE IS NO MANUAL PIN
--------------------------
Every other entity resolves a conflict by pinning a value as ``manual``,
which then outranks every provider. Not here. Pinning a duty status by
hand would make us a second system of record for hours of service, which
is the one thing this feature is built never to be — every surface says
so, and the AI tool refuses to compute a violation for the same reason.
``_refuse_pin`` therefore raises, loudly, rather than quietly accepting a
write that would turn a mirror into a record.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.telematics.catalog import PROVIDER_CATALOG
from adapters.telematics.protocol import Capability
from capabilities import source as reconciliation
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission

router = APIRouter(prefix="/eld", tags=["eld"])

_config = require_permission("can_manage_config_all")


#: The entity type, and the one field it arbitrates.
ELD_ENTITY = "driver_hos"
READING_FIELD = "reading"

#: "Whichever device reported most recently", as a selectable answer.
#:
#: Not a provider, a RULE — and it has to be offered rather than assumed.
#: Freshest-wins is the obvious default to an engineer and the wrong
#: default to an operator who has decided that one of their devices is
#: the one they trust: a newer reading from the device they are migrating
#: AWAY from would quietly outrank the one they moved to. Whose hours are
#: authoritative is the account owner's call, so it sits in the same
#: pick-list as the devices themselves.
NEWEST = "__newest__"


def _hos_providers() -> tuple[str, ...]:
    """Every catalog provider that offers hours of service, in catalog
    order — read from the CATALOG rather than the live registry so this
    module has a stable answer at import time and does not depend on
    which vendor packages happen to have been imported yet."""
    return tuple(
        pid for pid, entry in PROVIDER_CATALOG.items()
        if Capability.DRIVER_HOS in entry.capabilities
    )


async def _refuse_pin(db: Any, account_id: int, entity_id: Any,
                      field: str, value: Any) -> None:
    """Refuse to pin a duty status by hand.

    The hub's conflict UI resolves by writing a chosen value as
    ``manual``. Doing that here would put a human-authored duty status
    into a table every surface describes as a read-only mirror of a
    certified device — and the whole feature, down to the AI tool's
    refusal to compute a violation, rests on that not being true.
    """
    raise HTTPException(
        409,
        "Hours of service cannot be edited here. The connected ELD is the "
        "system of record; this is a read-only mirror of what it reported. "
        "Choose which device wins instead.",
    )


reconciliation.register_reconciled_entity(
    ELD_ENTITY,
    # ONE field: the whole reading. See the module docstring.
    fields=(READING_FIELD,),
    default_precedence={READING_FIELD: _hos_providers()},
    field_labels={READING_FIELD: "Duty reading"},
    # Catalog order first, then the rule — so an account that never opens
    # this panel behaves exactly as it does today.
    sources=_hos_providers() + (NEWEST,),
    apply_resolution=_refuse_pin,
)


class EldPrecedenceUpdate(BaseModel):
    #: ``{"reading": "orient_eld"}`` or ``{"reading": "__newest__"}``.
    primary: dict[str, str] = Field(default_factory=dict)


def _payload(options: dict) -> dict:
    """The panel's payload, with the rule labelled as what it is."""
    return {
        **options,
        # The pick-list carries devices AND one rule; a panel that
        # rendered "__newest__" raw would read as a broken provider id.
        "source_labels": {
            **{
                pid: (PROVIDER_CATALOG[pid].display_name
                      if pid in PROVIDER_CATALOG else pid)
                for pid in _hos_providers()
            },
            NEWEST: "Whichever reported most recently",
        },
        "applies_when": (
            "Only when one driver is reported by more than one connected "
            "ELD — normally each driver is on exactly one device, and both "
            "sets of readings are simply shown together."
        ),
    }


@router.get("/config")
async def get_config(user: dict = Depends(_config)):
    """The current choice and the choices available.

    The READ sits on the config flag with the write, as every other
    ``/<feature>/config`` does: its only consumer is the editor panel, so
    it is the settings themselves rather than a view of ELD data. Leaving
    it on the weaker permission would make the write gate decorative.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return _payload(
        await reconciliation.precedence_options(
            tenant, account_id, ELD_ENTITY),
    )


@router.put("/config")
async def put_config(
    body: EldPrecedenceUpdate,
    user: dict = Depends(_config),
):
    """Set which device wins when a driver is on two of them."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    if body.primary:
        await reconciliation.set_precedence(
            tenant, account_id, ELD_ENTITY, body.primary)
    return _payload(
        await reconciliation.precedence_options(
            tenant, account_id, ELD_ENTITY),
    )
