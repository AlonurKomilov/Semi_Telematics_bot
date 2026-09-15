"""ORIENT ELD as a ``TelematicsProvider``.

The vendor's words stop here.  Above this file nothing knows that
ORIENT spells a duty status ``"OFF_DUTY"``, that it puts the driver's
name in two fields, or that one of its UTC timestamps forgets to say
it is UTC.

What this provider claims, and what it refuses to claim
-------------------------------------------------------
It declares ``Capability.DRIVER_HOS`` — it genuinely is the account's
electronic logging device and it genuinely answers "what is this driver
doing right now".

It declares ``hos_clocks_reported = frozenset()`` — none of the four
countdowns.  That is not a gap waiting to be filled in: ORIENT's public
API has seven endpoints and not one of them carries remaining drive,
shift, cycle or break time.  Saying so in the declaration is what lets
Hours of Service write "ORIENT ELD reports duty status only" instead of
rendering four empty columns, which on a compliance page reads as four
zeroes and means the opposite.

Deriving the countdowns from what IS here would be the tempting move
and it is the wrong one.  ``status_activation_time`` plus a ruleset
would give you time driven, and time driven subtracted from a limit
would give you time remaining — but the limit depends on the ruleset in
force for that driver (US 70/8, 60/7, Canada, a short-haul exemption),
which the certified device knows and we do not.  A number computed from
the wrong limit is worse than a blank, because it looks like an answer.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from adapters.telematics.catalog import assert_declarations_agree
from adapters.telematics.protocol import (
    Capability,
    ConnectionStatus,
    DutyStatus,
    HosSnapshot,
    TelematicsProvider,
)

from .client import (
    MultiCompanyOrientClient,
    utc_iso,
    build_multi_company_orient_client,
)

logger = logging.getLogger(__name__)


# ORIENT's spellings → ours.  Keys are normalised to bare lowercase
# letters first (see ``_duty_status``), so ``OFF_DUTY``, ``off-duty``
# and ``Off Duty`` all arrive here as ``offduty`` and one entry covers
# every punctuation the vendor might change its mind about.
#
# Observed live: DRIVING, SLEEPER, OFF_DUTY, ON_DUTY.  The other two are
# FMCSA-mandated statuses that simply had nobody in them at the moment
# the account was sampled — mapping them now costs nothing and not
# mapping them would silently turn a driver on personal conveyance into
# an UNKNOWN the day one appears.
_DUTY_BY_VENDOR_SPELLING: dict[str, str] = {
    "offduty":             DutyStatus.OFF_DUTY,
    "off":                 DutyStatus.OFF_DUTY,
    "sleeper":             DutyStatus.SLEEPER,
    "sleeperberth":        DutyStatus.SLEEPER,
    "sb":                  DutyStatus.SLEEPER,
    "driving":             DutyStatus.DRIVING,
    "drive":               DutyStatus.DRIVING,
    "d":                   DutyStatus.DRIVING,
    "onduty":              DutyStatus.ON_DUTY,
    "ondutynotdriving":    DutyStatus.ON_DUTY,
    "on":                  DutyStatus.ON_DUTY,
    "personalconveyance":  DutyStatus.PERSONAL_CONVEYANCE,
    "pc":                  DutyStatus.PERSONAL_CONVEYANCE,
    "yardmove":            DutyStatus.YARD_MOVE,
    "ym":                  DutyStatus.YARD_MOVE,
}


def _duty_status(raw: Any) -> str:
    """One vendor spelling as one of ours.

    Anything unrecognised becomes ``UNKNOWN`` and never ``OFF_DUTY``.
    The difference matters more than it looks: a gap in this mapping
    resolved to off-duty would be indistinguishable from the device
    reporting a driver at rest, so a vendor renaming a status would
    quietly mark working drivers as resting.  ``UNKNOWN`` shows up as
    ``Unknown`` on the page and is a visible prompt to extend this map.
    """
    key = "".join(ch for ch in str(raw or "").lower() if ch.isalnum())
    if not key:
        return DutyStatus.UNKNOWN
    mapped = _DUTY_BY_VENDOR_SPELLING.get(key)
    if mapped is None:
        logger.warning(
            "orient_eld: unmapped duty status %r — reporting UNKNOWN", raw,
        )
        return DutyStatus.UNKNOWN
    return mapped


def _driver_name(row: dict) -> str:
    """The vendor's name for a driver, for diagnostics only.

    ORIENT splits it across ``name`` and ``surname``.  Display prefers
    OUR roster name wherever a link exists; this is what an operator
    sees for a driver nobody has linked yet, so it has to be a whole
    name rather than a first name.
    """
    first = str(row.get("name") or "").strip()
    last = str(row.get("surname") or "").strip()
    full = f"{first} {last}".strip()
    return full or str(row.get("username") or "").strip()


def to_snapshot(row: dict) -> HosSnapshot | None:
    """One tracking row as one canonical snapshot, or ``None`` to skip.

    A row with no ``driver_id`` is skipped rather than given a made-up
    key: the id is what the store keys on and what a future roster link
    attaches to, and inventing one would create a driver that can never
    be linked and never be replaced by the real row.

    ``source_ts`` is the TELEMETRY time, not the status time and not our
    fetch time.  ORIENT can only know a duty status from the device's
    last upload, so the age of that upload is the honest age of the
    reading — and it is the number that decides whether Hours of
    Service shows this row as stale.
    """
    pdid = row.get("driver_id")
    if pdid in (None, ""):
        return None
    return HosSnapshot(
        provider_driver_id=str(pdid),
        duty_status=_duty_status(row.get("status")),
        # Every clock stays None — see the module docstring.  This is a
        # declaration, not an oversight, and ``hos_clocks_reported``
        # below is the machine-readable half of the same statement.
        drive_remaining_seconds=None,
        shift_remaining_seconds=None,
        cycle_remaining_seconds=None,
        break_in_seconds=None,
        last_status_change=utc_iso(row.get("status_activation_time_utc")),
        source_ts=(
            utc_iso(row.get("location_datetime_utc"))
            or utc_iso(row.get("datetime_utc"))
        ),
        driver_name=_driver_name(row),
        # The truck the device says they are on.  Display only — a bare
        # unit number cannot decide scope, because two companies in one
        # account run the same numbers.
        provider_vehicle=str(row.get("vehicle_number") or "").strip(),
        # Which of OUR companies this key belongs to.  The fan-out tags
        # every row with it; dropping it here is what would leave five
        # carriers' drivers in one undifferentiated list.
        company_code=str(row.get("_company_code") or ""),
    )


class OrientEldProvider:
    """``TelematicsProvider`` implementation for ORIENT ELD."""

    provider_id: str = "orient_eld"

    supported_capabilities: frozenset[str] = frozenset({
        Capability.DRIVER_HOS,
        Capability.VEHICLE_SPEC,
    })

    # The empty set is the whole point of this declaration existing.
    hos_clocks_reported: frozenset[str] = frozenset()

    def __init__(self, client: MultiCompanyOrientClient) -> None:
        self._client = client
        self._owned_by_test = False

    @property
    def client(self) -> MultiCompanyOrientClient:
        return self._client

    @classmethod
    async def build_for_test(
        cls, account_id: int, creds: dict[str, Any],
    ) -> "OrientEldProvider":
        """A single-use provider over raw credentials, for the connect probe.

        There is no integration row yet on a first connect, so the
        cached resolver path has nothing to read — the fan-out is built
        straight from what the operator just typed.
        """
        client = build_multi_company_orient_client(
            creds or {}, account_id=account_id,
        )
        if not len(client):
            raise ValueError(
                "orient_eld requires an API key — either "
                "'api_key' for a single company, or a 'companies' map "
                "of company code to key.",
            )
        instance = cls(client)
        instance._owned_by_test = True
        return instance

    async def close_if_owned_by_test(self) -> None:
        if self._owned_by_test:
            await self._client.close()

    # ── Lifecycle ─────────────────────────────────────────────────

    async def test_connection(self, creds: dict[str, Any]) -> ConnectionStatus:
        ok, message, meta = await self._client.test_connection()
        return ConnectionStatus(
            ok=ok,
            message=message,
            # ORIENT has no "org id"; the DOT number is what an operator
            # can actually cross-reference in the vendor's own portal —
            # and what lets the per-company probe prove this key belongs
            # to the company whose row it was pasted into.
            provider_account_id=str((meta or {}).get("dot_number") or ""),
            provider_account_id_kind="usdot",
        )

    async def close(self) -> None:
        await self._client.close()

    # ── The one feed this vendor serves ──────────────────────────

    async def get_driver_hos(self) -> list[HosSnapshot]:
        """Every driver ORIENT is currently reporting, in our vocabulary.

        Rows the vendor cannot key are dropped here rather than deeper:
        the store would refuse them anyway, and dropping them at the
        adapter keeps the count the ingest logs honest.

        The duplicate check exists because ``driver_hos_live`` is keyed
        on ``(account_id, provider_id, provider_driver_id)`` with no
        company in it.  On an account running ONE ORIENT company that
        cannot bite.  On five it could: two companies reporting the same
        ``driver_id`` would have one driver's duty status overwrite the
        other's, silently, on a compliance surface.

        ORIENT's own API says that cannot happen — ``/api/logs/tracking``
        accepts ``driver_id`` as a top-level filter ALONGSIDE
        ``dot_number``/``mc_number``, which only works if the id is
        unique across companies, and the live ids bear that out (26
        drivers in one company spread over 6956..19192, a
        platform-wide sequence rather than a per-company one).

        So this is a tripwire, not a workaround.  If it ever fires, the
        assumption above is wrong and the honest fix is a composite id
        — but nothing is lost in the meantime: the second company's
        driver keeps its own row under a disambiguated key instead of
        erasing the first.
        """
        rows = await self._client.get_tracking()
        out: list[HosSnapshot] = []
        seen: dict[str, str] = {}
        skipped = 0
        duplicates = 0
        for row in rows:
            snap = to_snapshot(row)
            if snap is None:
                skipped += 1
                continue
            pdid = snap.provider_driver_id
            company = str(row.get("_company_code") or "")
            first = seen.get(pdid)
            if first is None:
                seen[pdid] = company
            elif first == company:
                # The same company listed the same driver twice — one
                # driver, one row.  Nothing to disambiguate.
                duplicates += 1
                continue
            else:
                logger.error(
                    "orient_eld: driver_id %s reported by BOTH company %s "
                    "and company %s — ids were assumed unique across "
                    "companies. Keeping both under distinct keys; the "
                    "store's key has no company in it, so without this "
                    "one would have overwritten the other.",
                    pdid, first, company,
                )
                snap = dataclasses.replace(
                    snap, provider_driver_id=f"{pdid}@{company}",
                )
            out.append(snap)
        if skipped:
            logger.warning(
                "orient_eld: %d tracking row(s) had no driver_id — skipped",
                skipped,
            )
        if duplicates:
            logger.info(
                "orient_eld: %d repeated row(s) within a company collapsed",
                duplicates,
            )
        return out

    async def get_vehicle_spec(self) -> list[dict[str, Any]]:
        """What ORIENT knows each truck IS — a SECOND opinion.

        ``/api/vehicles`` carries VIN, plate, make and model for the
        same trucks another integration already registered, which is
        exactly the shape ``capabilities/source`` arbitrates: several
        sources describing one record, field by field, with the account
        owner choosing whose answer wins.

        Deliberately NOT mapped:

        ``year`` — ORIENT does not report one, and a blank is already
        "no opinion" to the merge.

        ``device_id`` -> ``gateway_serial`` — tempting and wrong. That
        column holds a Samsara gateway's hardware serial; ORIENT's
        device id is an integer in ORIENT's own namespace. Writing one
        into the other would make two unrelated identifiers collide in
        a field the device-identity watch compares across ticks.
        """
        rows = await self._client.get_vehicles()
        out: list[dict[str, Any]] = []
        for row in rows:
            unit = str(row.get("unit_number") or "").strip()
            if not unit:
                # The registry matches on the unit number; a row without
                # one cannot be attached to anything and inventing a key
                # would create the duplicate this feed exists to avoid.
                continue
            out.append({
                "unit_number": unit,
                "vin": str(row.get("vin") or "").strip().upper(),
                "plate_number": str(row.get("plate") or "").strip(),
                "make": str(row.get("make") or "").strip(),
                "model": str(row.get("model") or "").strip(),
                "company_code": str(row.get("_company_code") or "").strip(),
            })
        return out

    # ── Not this vendor's business ───────────────────────────────
    #
    # ORIENT's public API does expose vehicle locations, and we
    # deliberately do NOT claim VEHICLE_STATE from it here.  That feed
    # writes the live map and the vehicle registry, an account can run
    # a telematics provider and this ELD at the same time, and
    # resolving two writers for one table is a decision with its own
    # consequences — not something to acquire as a side effect of
    # connecting an ELD.  The catalog claims one capability; these
    # return empty so the runtime protocol check still passes.

    async def get_vehicles_overview(self) -> list[dict[str, Any]]:
        return []

    async def get_safety_events(self) -> list[dict[str, Any]]:
        return []

    async def get_vehicle_health(self) -> list[dict[str, Any]]:
        return []

    async def get_vehicle_faults(self) -> list[dict[str, Any]]:
        return []

    async def get_stats_history(
        self,
        types: list[str],
        start_iso: str,
        end_iso: str,
    ) -> dict[str, dict[str, Any]]:
        return {}


# Compile-time protocol satisfaction check.
_PROVIDER_PROTOCOL_CHECK: type[TelematicsProvider] = OrientEldProvider

# Catalog + clock-declaration drift guard.  Shared with every other
# provider so the invariant cannot be half-implemented here.
assert_declarations_agree(OrientEldProvider)
