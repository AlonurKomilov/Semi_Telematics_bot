"""Vendor-neutral telematics provider contract.

Every integration (Samsara, Motive, Geotab, Datatruck, ...) implements
this protocol.  The shape mirrors what the scheduler and capabilities
layer already need from a Samsara client today — once a provider
honours these methods, the rest of the system never has to know which
vendor is on the other side.

Capabilities
------------
A provider declares which of the well-known capabilities it supports
through ``supported_capabilities``.  The scheduler and the dashboard
both read this list:

  * Scheduler — skips ingest jobs for capabilities the provider doesn't
    support, regardless of the per-account toggle state.
  * Dashboard — only renders toggles for capabilities the connected
    provider actually offers, so owners don't see options that would
    never do anything.

A new capability becomes visible to operators the moment a provider
declares it in ``supported_capabilities``.  Removing a capability from
a provider's set silently disables it for every account using that
provider — by design, since the upstream no longer exposes it.

Credentials
-----------
Each provider's credential shape is opaque to this module.  The
account_integrations row stores it as an encrypted JSON string; the
provider's ``test_connection`` and ``build_client`` methods receive
the decrypted dict and decide what to do with it.  This is why the
protocol takes ``creds: dict`` rather than typed fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


# ── Well-known capability identifiers ─────────────────────────────────
#
# Capability ids are stable strings that appear in account_integrations.
# feature_toggles and in dashboard rendering.  When adding a new
# capability, add it here AND to the catalog so the dashboard knows what
# label to show.  Providers that don't support a capability simply omit
# it from their ``supported_capabilities`` set.

class Capability:
    """Canonical capability identifiers.

    Defined as class attributes so callers get IDE autocompletion and
    typos surface as AttributeError instead of silent miss.
    """

    VEHICLE_STATE           = "vehicle_state"
    SAFETY_EVENTS           = "safety_events"
    VEHICLE_HEALTH          = "vehicle_health"
    VEHICLE_FAULTS          = "vehicle_faults"
    DRIVER_EFFICIENCY_DAILY = "driver_efficiency"
    DRIVER_HOS              = "driver_hos"
    FLEET_WEATHER           = "fleet_weather"
    FLEET_EFFICIENCY        = "fleet_efficiency"
    GEOFENCE_DEFINITIONS    = "geofence_definitions"
    STATE_SNAPSHOT_HISTORY  = "state_snapshot_history"
    TELEMETRY_HOURLY        = "telemetry_hourly"
    METRICS_DAILY           = "metrics_daily"
    HISTORY_PRUNE           = "history_prune"
    HISTORY_BACKFILL        = "history_backfill"

    # ── TMS (Transportation Management System) capabilities ─────
    # These cover providers like Datatruck whose API is dispatch-
    # shaped rather than telemetry-shaped — they have no live GPS
    # or safety events, but they DO have authoritative drivers,
    # trucks, orders/loads, and work orders.  A provider declaring
    # any of these in supported_capabilities triggers TMS-shaped
    # sync jobs rather than the live-state ingest jobs.
    TMS_DRIVERS_SYNC        = "tms_drivers_sync"
    TMS_TRUCKS_SYNC         = "tms_trucks_sync"
    TMS_TRAILERS_SYNC       = "tms_trailers_sync"
    TMS_ORDERS_SYNC         = "tms_orders_sync"
    TMS_WORK_ORDERS_SYNC    = "tms_work_orders_sync"


class DutyStatus:
    """The canonical duty statuses, owned HERE and not by any vendor.

    Every ELD names these differently — Samsara says ``offDuty`` and
    ``sleeperBerth``, the FMCSA regulation says "off duty" and "sleeper
    berth", the next provider will say something else again.  The
    mapping is each adapter's job; this set is what the rest of the
    system is allowed to see, so a feature never learns a vendor's
    spelling.

    ``PERSONAL_CONVEYANCE`` and ``YARD_MOVE`` are real FMCSA
    sub-statuses of off-duty and on-duty respectively, not
    decorations: a driver moving under personal conveyance is off
    duty, and treating that as driving would report a violation that
    does not exist.

    ``UNKNOWN`` is the honest answer for a status we could not map,
    and it must stay in the set: silently coercing an unrecognised
    value to ``OFF_DUTY`` would turn a gap in our mapping into a
    statement that a driver was resting.
    """

    OFF_DUTY            = "off_duty"
    SLEEPER             = "sleeper"
    DRIVING             = "driving"
    ON_DUTY             = "on_duty"
    PERSONAL_CONVEYANCE = "personal_conveyance"
    YARD_MOVE           = "yard_move"
    UNKNOWN             = "unknown"

    ALL: frozenset[str] = frozenset({
        OFF_DUTY, SLEEPER, DRIVING, ON_DUTY,
        PERSONAL_CONVEYANCE, YARD_MOVE, UNKNOWN,
    })


class HosClock:
    """The four countdown clocks, named so a provider can say which of
    them it actually reports.

    ``supported_capabilities`` answers *do we fetch hours of service
    from this provider* — a yes/no about the FEED.  This answers a
    different question the same declaration cannot: *which of the four
    numbers does this vendor ever put in the payload*.  They are not the
    same question, and the second one has an answer before a single row
    is fetched.

    It has to be declared rather than observed.  Deriving it from
    ingested rows conflates three unrelated things: a vendor that never
    reports a clock, a driver who has not started their day, and a
    driver the ELD has no session for — so the set would change shape
    with who happens to be on shift.  Worse, a mapping bug in an adapter
    that silently produced ``None`` would HIDE the broken column instead
    of showing it.  Declared, the same bug is a failing test.

    Empty is a legitimate answer.  An ELD that reports duty status and
    nothing else is a real product, not a broken integration, and the
    surfaces above must be able to say "this device does not report
    remaining hours" instead of showing four blanks that read as zero.
    """

    DRIVE = "drive"
    SHIFT = "shift"
    CYCLE = "cycle"
    BREAK = "break"

    ALL: frozenset[str] = frozenset({DRIVE, SHIFT, CYCLE, BREAK})

    FIELDS: dict[str, str] = {
        DRIVE: "drive_remaining_seconds",
        SHIFT: "shift_remaining_seconds",
        CYCLE: "cycle_remaining_seconds",
        BREAK: "break_in_seconds",
    }
    """Clock id → the :class:`HosSnapshot` field it fills.  Keeping the
    two names joined here is what lets a guard check a provider's claim
    against the snapshots it actually returns."""


@dataclass(frozen=True)
class HosSnapshot:
    """One driver's hours-of-service clocks, as one provider reports them.

    A SNAPSHOT, deliberately — not a log.  The certified ELD is the
    system of record for hours of service; we hold a read-only mirror
    of what it currently says, so that dispatch can ask "who can take
    this load" without opening another product.  Nothing here is
    evidence, and nothing downstream may compute a violation from it.

    ``source_ts`` is when the PROVIDER observed this, and it is kept
    apart from our own write time on purpose.  HOS goes stale in
    minutes: a reading we fetched thirty seconds ago can describe a
    driver who has been driving for the last twenty, and a surface
    that shows our write time would call that fresh.

    Seconds rather than hours throughout, because the regulation's
    limits are not all whole hours and every provider reports integer
    seconds — converting at the edge loses precision nobody can get
    back.  ``None`` means the provider did not report that clock, which
    is different from zero: zero is "out of hours".
    """

    provider_driver_id: str
    """The vendor's own id for the driver — matched to our roster
    through the account's existing driver link, never used as an
    identity of its own."""

    duty_status: str = DutyStatus.UNKNOWN

    # Four clocks, and every one of them counts DOWN.
    #
    # This used to carry two "today" fields holding time USED, which was
    # wrong in the most dangerous direction available: an ELD reports
    # what is LEFT, so a driver with one hour of drive time remaining —
    # ten hours driven — was being recorded as having driven one hour.
    # A dispatcher reading that concludes the opposite of the truth.
    #
    # Turning "remaining" into "used" needs the ruleset's limit (US 70/8
    # vs 60/7 vs Canada vs a short-haul exemption), which is exactly the
    # computation this feature refuses to do: the certified device knows
    # the ruleset and we do not.  So we store what the device reports and
    # nothing else.
    drive_remaining_seconds: int | None = None
    shift_remaining_seconds: int | None = None
    cycle_remaining_seconds: int | None = None
    break_in_seconds: int | None = None
    """Until the mandatory 30-minute break is due.  A real fourth clock,
    not a derivation — a driver with hours left on every other clock
    still has to stop for this one."""

    last_status_change: str = ""
    """When the current duty status began.  Often blank: it is not on
    every provider's current-status payload, and a blank is the honest
    answer rather than a guess at the poll time."""

    source_ts: str = ""
    driver_name: str = ""
    """Vendor-reported name, for diagnostics when a link is missing.
    Display always prefers OUR roster name."""

    provider_vehicle: str = ""
    """The truck the DEVICE says this driver is on, in the vendor's own
    words — kept apart from our roster's ``truck_num`` on purpose.

    ORIENT sends it on every tracking row and we were dropping it, so
    an account with sixty-nine unlinked drivers showed sixty-nine
    dashes in the Truck column — on a page where "which truck" is half
    of what dispatch is asking.

    It is for DISPLAY, and it is NOT a scope rung.  A bare unit number
    cannot decide who may see a driver: numbers are reused across the
    companies inside one account, so "103" names two trucks and
    admitting this as identity would hand a company-restricted
    dispatcher the other company's driver.  Scope stays on the vehicle
    identity ladder, which splits the twins.

    Blank is normal and honest — Samsara's clocks endpoint carries no
    vehicle, so its rows leave this empty and the surface falls back to
    what our own roster knows."""

    company_code: str = ""
    """Which of OUR companies this reading belongs to.

    An account is several legal carriers, and every vendor that issues
    a key per company already knows which one a row came from — the
    Samsara fan-out tags each row ``_org`` and the ORIENT one tags
    ``_company_code``.  Both used to drop it here, which was invisible
    while an account ran one company and became the whole problem at
    five: a hundred drivers from five separate carriers in one
    undifferentiated list, with no way to say whose driver is whose.

    OUR code, not the vendor's, so it joins straight to ``companies``
    — the same column every other grid on the platform shows as
    "Company".

    Blank is honest and expected: a single-company account, or a
    provider whose payload carries no company.  It is NOT a scope rung.
    Scope is decided by the vehicle identity ladder, which splits the
    twins a bare name cannot; adding a second rung here would quietly
    widen who can see a named driver's duty status."""


@dataclass(frozen=True)
class ConnectionStatus:
    """Result of a ``test_connection`` call.

    ``ok`` is the single boolean ops cares about; ``message`` carries
    a human-readable detail (a Samsara error code, an OAuth scope
    mismatch, an HTTP 401, etc.) the dashboard renders verbatim.
    ``provider_account_id`` is the upstream's own identifier for the
    connection — useful for ops to cross-reference in the vendor's
    own admin panel.
    """

    ok: bool
    message: str = ""
    provider_account_id: str = ""

    provider_account_id_kind: str = ""
    """What ``provider_account_id`` actually IS, when it is something we
    can check against our own records.

    ``"usdot"`` means it is the carrier's USDOT number, which
    ``companies.usdot_number`` also holds — so a caller can prove that
    the key an operator pasted into the row labelled CFT really belongs
    to CARGO FREIGHT TRUCKING, rather than to one of the other four
    carriers on the account.

    Nothing automatic can make that mistake: a key is never lent from
    one company to another.  A PERSON can, by pasting into the wrong
    row, and the result is the worst shape available — another
    carrier's drivers reported under this one's name, with every
    surface agreeing.

    Empty means the id is not checkable (Samsara's is an org id, which
    matches nothing we store), and an empty kind must be read as "no
    opinion", never as "mismatch"."""


@runtime_checkable
class TelematicsProvider(Protocol):
    """The contract every provider implementation must satisfy.

    Implementations live under ``adapters/telematics/<provider_id>/``.
    They register themselves via ``register_provider`` in the
    registry module so the scheduler and dashboard can resolve them
    by ``provider_id``.

    Methods that mirror Samsara stat-fetches return shape-compatible
    payloads — the same dicts the existing capabilities layer
    already consumes.  This keeps the migration zero-risk: nothing
    downstream changes when we swap the implementation.
    """

    # ── Identity ──────────────────────────────────────────────────

    provider_id: str
    """Stable identifier matching the account_integrations row."""

    supported_capabilities: frozenset[str]
    """The set of ``Capability.*`` values this provider implements."""

    hos_clocks_reported: frozenset[str]
    """Which of :class:`HosClock`'s four countdowns this provider ever
    populates.

    Invariants, enforced by each adapter's own import-time guard:

      * a subset of ``HosClock.ALL``;
      * EMPTY when the provider does not declare
        ``Capability.DRIVER_HOS`` — nothing to report if we never ask;
      * MAY be empty when it does.  ORIENT ELD is the worked example:
        it reports duty status and when it began, and no countdown at
        all.

    Read this through the registry, never by comparing a provider id.
    A surface that says "which clocks am I able to show" must keep
    working when the third ELD reports two of the four.
    """

    # ── Lifecycle ─────────────────────────────────────────────────

    async def test_connection(self, creds: dict[str, Any]) -> ConnectionStatus:
        """Validate credentials without persisting anything.

        Called when an owner clicks "Test connection" in the
        dashboard, and periodically by the health-check job.  Must
        complete in under 10 seconds or return ``ok=False`` — the
        dashboard times out at 12s.
        """
        ...

    async def close(self) -> None:
        """Release HTTP sessions / connection pools.  Idempotent."""
        ...

    # ── Live state ────────────────────────────────────────────────

    async def get_vehicles_overview(self) -> list[dict[str, Any]]:
        """Current snapshot of every vehicle in the fleet.

        Shape: list of dicts whose required keys are
        ``id, name, _org`` and whose optional keys cover location /
        engine state / fuel / odometer / engine hours.  See the
        Samsara implementation for the canonical reference shape.
        """
        ...

    async def get_safety_events(self) -> list[dict[str, Any]]:
        """Safety events since the last poll."""
        ...

    async def get_vehicle_health(self) -> list[dict[str, Any]]:
        """Current health stats per vehicle (battery / oil / coolant
        / engine load / RPM / etc.)."""
        ...

    async def get_vehicle_faults(self) -> list[dict[str, Any]]:
        """Vehicles with at least one active fault code."""
        ...

    async def get_driver_hos(self) -> list[HosSnapshot]:
        """Every driver's current hours-of-service clocks.

        Returns TYPED snapshots, not the vendor's dicts — the one
        method on this protocol that does, because hours of service is
        the one feed where a vendor's spelling reaching a feature would
        be a compliance-shaped bug rather than a cosmetic one.  The
        adapter maps duty statuses into :class:`DutyStatus` and
        normalises every clock to seconds.

        Providers without ELD access return ``[]``.  Callers must read
        that as "this provider does not report hours", never as "every
        driver has hours remaining" — which is why the capability, not
        the emptiness of this list, is what decides whether we answer
        an HOS question at all.
        """
        ...

    # ── Historical ───────────────────────────────────────────────

    async def get_stats_history(
        self,
        types: list[str],
        start_iso: str,
        end_iso: str,
    ) -> dict[str, dict[str, Any]]:
        """Per-vehicle historical stat samples over a window.

        Used by the vehicle-history backfill capability.  Returns a
        dict keyed by vehicle id; each value is a dict whose keys are
        the requested type names mapped to lists of
        ``{time, value}`` data points.  Providers that lack history
        access return an empty dict — the caller treats that as
        "backfill not supported on this provider".
        """
        ...
