"""Provider catalog.

A read-only, code-defined inventory of every telematics provider the
platform knows about.  The dashboard renders one card per entry; the
"Connect" button on a card calls the matching registered provider's
auth flow.  Entries with ``status=ProviderStatus.COMING_SOON`` render
as inert previews — the catalog acknowledges them so owners can see
the roadmap, but no client implementation exists yet.

Adding a new provider here is a metadata-only change.  Wiring its
actual client up requires:

  1. Creating ``adapters/telematics/<provider_id>/client.py`` that
     implements ``TelematicsProvider``.
  2. Calling ``register_provider(provider_id, ClientClass)`` from that
     module's ``__init__.py``.
  3. Flipping this catalog entry from COMING_SOON to AVAILABLE.

Everything downstream — scheduler jobs, capabilities, dashboard
routes, owner-facing toggles — picks the new provider up
automatically because they all read from the registry, not from
hardcoded vendor imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .protocol import Capability


class ProviderStatus(str, Enum):
    """Lifecycle state of a provider in the catalog."""

    AVAILABLE   = "available"     # client implemented, owners can connect
    BETA        = "beta"          # client implemented, gated to owner consent
    COMING_SOON = "coming_soon"   # metadata-only, no client yet
    DEPRECATED  = "deprecated"    # client still works but new connects blocked


class ProviderKind(str, Enum):
    """Shape of data the provider exposes.

    ``telematics`` providers (Samsara, Motive, Geotab) push live
    vehicle state, safety events, fault codes — fits the per-company-
    key + history-backfill UX with a "Test connection" probe.

    ``tms`` providers (Datatruck) expose dispatch-shaped data —
    drivers, trucks, loads, work orders — sourced from one per-tenant
    token.  No live telemetry, no per-company keys, no history backfill;
    the UI renders a different card body for these.

    Each kind owns its own router file under
    ``capabilities/integrations/<kind>/router.py`` so adding a new
    provider of one kind never touches the other kind's surface.
    """

    TELEMATICS = "telematics"
    TMS        = "tms"


@dataclass(frozen=True)
class FeedSpec:
    """One data feed a provider pushes into the tenant DB — the unit the
    Integration card's shared "Synced data" table renders.

    Declaring feeds here (not in a router) makes the card data-driven:
    a new provider gets its synced-data table for free by listing its
    feeds, with no new endpoint or UI.

      * ``capability`` ties the feed to its on/off toggle, and the
        frontend derives the row label + cadence from it.
      * ``table`` / ``ts_col`` are the tenant-DB source the generic
        ``/{provider}/feeds`` endpoint reads count + last-synced from.
      * ``mode`` hints which action the card offers
        (``scheduled+backfill`` → "Refresh history"; plain
        ``scheduled`` → none, since continuous feeds have no manual pull).
    """

    capability: str
    table: str
    ts_col: str
    mode: str = "scheduled"
    # The product FEATURE this data primarily serves (display grouping on
    # the integration card's "Synced data" table).  Telemetry streams are
    # COMPONENTS of a feature (faults/health are parts of Vehicles), not
    # features themselves — this records which feature reads them.  The
    # integration stays feature-agnostic (it only fills the warehouse);
    # this is metadata for grouping, not a dependency.
    feature: str = ""
    # The sub-component WITHIN the feature (e.g. Vehicles → Location & state
    # / Telemetry / Health / Faults).  Drives the card's second grouping
    # level so the telemetry tiers read as one component instead of separate
    # feeds.  Empty → the feed renders directly under its feature.
    component: str = ""


@dataclass(frozen=True)
class ProviderCatalogEntry:
    """Metadata the dashboard needs to render a provider card.

    The dashboard never imports a vendor module directly — it reads
    from this catalog plus the registry, so adding a provider is a
    metadata-only change for the UI.
    """

    provider_id: str
    display_name: str
    tagline: str
    """One-line description shown beneath the card title."""

    description: str
    """Longer paragraph rendered when the card is expanded."""

    capabilities: frozenset[str]
    """The well-known capabilities this provider implements when
    AVAILABLE.  Determines which feature toggles the dashboard offers
    for connected accounts.  Listed even for COMING_SOON entries so
    owners can preview what they'll get."""

    auth_kind: str
    """Hint to the dashboard about which credential form to render.
    Currently ``api_token`` is the only kind; OAuth2-based providers
    will use ``oauth2`` and ship their own auth-redirect flow."""

    docs_url: str = ""
    """Optional vendor documentation link rendered as a help icon."""

    icon: str = ""
    """Optional lucide-react icon name or asset path — fallback is
    the generic Plug icon."""

    status: ProviderStatus = ProviderStatus.AVAILABLE

    kind: ProviderKind = ProviderKind.TELEMATICS
    """Whether the provider is telematics-shaped (live vehicle state)
    or TMS-shaped (dispatch/orders/drivers).  Picks which router
    file owns the provider's routes and which frontend card body
    gets rendered.  Defaults to TELEMATICS to keep existing entries
    unchanged."""

    feature_defaults: dict[str, dict] = field(default_factory=dict)
    """Default per-capability config (cadence, etc.) applied when an
    owner first connects this provider.  Stored verbatim in
    account_integrations.feature_toggles; owners can override later."""

    feeds: tuple[FeedSpec, ...] = ()
    """Data feeds this provider pushes into the tenant DB — drive the
    card's shared "Synced data" freshness table via the generic
    ``/{provider}/feeds`` endpoint.  Empty for providers whose synced-
    data view is bespoke (e.g. Datatruck's interactive sync panel)."""


# Canonical defaults for Samsara — match the live scheduler cadences.
_SAMSARA_DEFAULTS: dict[str, dict] = {
    Capability.VEHICLE_STATE:           {"enabled": True, "interval_sec": 60},
    Capability.SAFETY_EVENTS:           {"enabled": True, "interval_min": 5},
    Capability.VEHICLE_HEALTH:          {"enabled": True, "interval_min": 5},
    Capability.VEHICLE_FAULTS:          {"enabled": True, "interval_min": 2},
    Capability.DRIVER_EFFICIENCY_DAILY: {"enabled": True, "interval_hour": 1},
    Capability.FLEET_WEATHER:           {"enabled": True, "interval_min": 10},
    Capability.FLEET_EFFICIENCY:        {"enabled": True, "interval_min": 30},
    Capability.GEOFENCE_DEFINITIONS:    {"enabled": True, "interval_hour": 6},
    # NB: the snapshot/hourly/daily roll-ups are NOT Samsara capabilities —
    # they're provider-agnostic warehouse plumbing (always-on, ungated), so
    # they don't belong in a provider's toggle set.  Their cadence lives with
    # the warehouse (the scheduler), not here.  See capabilities/warehouse.
    # NB: no HISTORY_PRUNE — retention is now owned by the cross-cutting
    # Retention hub (the ``data_retention`` job + the operator Retention
    # page), not a per-account integration toggle.  The old per-account
    # prune capability was retired in that cutover; leaving it here would
    # render a dead "History retention prune" toggle that controls nothing.
    # ON by default so a fresh connect automatically backfills 30
    # days of Samsara history.  Without this the calendar projection
    # would sit empty for ~7 days while the live aggregators caught
    # up — owners would think the system was broken.  The throttle
    # (1 RPS) and global serial-backfill lock cap the upstream load
    # regardless of how many accounts connect at once.
    Capability.HISTORY_BACKFILL:        {"enabled": True},
}


# Samsara's data feeds → the card's "Synced data" table.  Order = display
# order.  ``(table, ts_col)`` is the tenant-DB freshness source the generic
# /feeds endpoint reads.  Meta capabilities (prune/backfill) are NOT feeds —
# they're actions, not stored data.
#
# ``ts_col`` is the INGEST-time column ("when we last synced this"), NOT a
# source/event/period time — so "last sync" reflects job liveness, not the
# truck's GPS time.  Overwrite tables use ``updated_at`` (stamped now on each
# upsert); roll-up / append tables use ``ingested_at``; the 5-min snapshot's
# ``captured_at`` is itself the capture (ingest) time.  Without this, e.g.
# Vehicle health showed "15h ago" overnight because its ``captured_at`` is the
# parked truck's GPS time, even though the 5-min sync was running fine.
_SAMSARA_FEED_SPECS: tuple[FeedSpec, ...] = (
    # Ordered so feeds of the same feature are contiguous → clean groups.
    # The 5-min / hourly / daily TELEMETRY tiers are NOT feeds — they're our
    # provider-agnostic downsampling cascade (warehouse plumbing), surfaced on
    # the operator console (/system/accounts/{id}/telemetry), not the
    # customer's integration card.  See capabilities/warehouse.
    FeedSpec(Capability.VEHICLE_STATE,           "vehicle_state",            "updated_at",               feature="Vehicles",   component="Location & state"),
    FeedSpec(Capability.VEHICLE_HEALTH,          "vehicle_health_snapshot",  "updated_at",               feature="Vehicles",   component="Health"),
    FeedSpec(Capability.VEHICLE_FAULTS,          "vehicle_fault_snapshot",   "updated_at",               feature="Vehicles",   component="Faults"),
    FeedSpec(Capability.SAFETY_EVENTS,           "safety_event_log",         "ingested_at",              feature="Safety",     component="Events"),
    FeedSpec(Capability.DRIVER_EFFICIENCY_DAILY, "driver_efficiency_daily",  "ingested_at",              feature="Scorecards", component="Efficiency"),
    FeedSpec(Capability.FLEET_WEATHER,           "aggregate_weather_snapshot",    "updated_at",          feature="Live Map",   component="Weather"),
    FeedSpec(Capability.FLEET_EFFICIENCY,        "aggregate_efficiency_snapshot", "updated_at",          feature="Costs",      component="Efficiency"),
    FeedSpec(Capability.GEOFENCE_DEFINITIONS,    "geofence_definitions",     "updated_at",               feature="Geofences",  component="Definitions"),
)


def resolve_capability_cadence(
    provider_id: str,
    capability: str,
    cadence_overrides: dict | None = None,
) -> dict:
    """Resolve the effective cadence config for a (provider, capability)
    pair given the per-account ``cadence_overrides`` from the
    integration row.

    Returns the override dict when one is present and non-empty;
    otherwise returns the catalog default for the capability;
    otherwise an empty dict.  Callers inspect the returned shape
    (``interval_sec`` / ``interval_min`` / ``interval_hour`` / ``cron``)
    to translate to APScheduler arguments.

    The APScheduler instance itself runs at the catalog default
    cadence — per-account override of the scheduler tick rate would
    require one scheduler per account, which we don't want.  This
    helper is used by the dashboard's "Cadence: every X" display so
    owners see the value that will actually be honoured by the per-
    tick check.  Faster-than-default overrides are silently capped at
    the catalog default for now; the dashboard surfaces this so the
    setting never looks like it's silently ignored.
    """
    if cadence_overrides:
        override = cadence_overrides.get(capability) or {}
        if isinstance(override, dict) and override:
            return override
    entry = PROVIDER_CATALOG.get(provider_id)
    if entry is None:
        return {}
    return dict(entry.feature_defaults.get(capability) or {})


PROVIDER_CATALOG: dict[str, ProviderCatalogEntry] = {
    "samsara": ProviderCatalogEntry(
        provider_id="samsara",
        display_name="Samsara",
        tagline="Live telematics, safety events, maintenance history",
        description=(
            "Connects to Samsara's fleet platform for live vehicle "
            "state, safety events, fault codes, driver efficiency, "
            "and historical stat backfill.  Each company in your "
            "account uses its own Samsara API key — manage them "
            "under Companies."
        ),
        capabilities=frozenset(_SAMSARA_DEFAULTS.keys()),
        auth_kind="api_token",
        docs_url="https://developers.samsara.com/",
        icon="Satellite",
        status=ProviderStatus.AVAILABLE,
        feature_defaults=_SAMSARA_DEFAULTS,
        feeds=_SAMSARA_FEED_SPECS,
    ),

    # ── Coming-soon placeholders ──
    #
    # Visible in the dashboard catalog so owners see the roadmap.
    # Each entry is metadata-only — connecting them is blocked
    # until the implementing client lands and registers itself.

    "motive": ProviderCatalogEntry(
        provider_id="motive",
        display_name="Motive",
        tagline="ELD, driver workflow, asset GPS",
        description=(
            "Motive (formerly KeepTruckin) integration covering "
            "electronic logs, dispatch workflow, and GPS tracking.  "
            "Coming soon."
        ),
        capabilities=frozenset({
            Capability.VEHICLE_STATE,
            Capability.SAFETY_EVENTS,
            Capability.DRIVER_EFFICIENCY_DAILY,
            Capability.STATE_SNAPSHOT_HISTORY,
        }),
        auth_kind="api_token",
        docs_url="https://developer.gomotive.com/",
        icon="Plug",
        status=ProviderStatus.COMING_SOON,
    ),

    "geotab": ProviderCatalogEntry(
        provider_id="geotab",
        display_name="Geotab",
        tagline="MyGeotab data feeds — faults, idle, fuel, GPS",
        description=(
            "Geotab MyGeotab integration covering vehicle telemetry, "
            "fault codes, fuel usage, and engine state.  "
            "Coming soon."
        ),
        capabilities=frozenset({
            Capability.VEHICLE_STATE,
            Capability.VEHICLE_FAULTS,
            Capability.SAFETY_EVENTS,
            Capability.STATE_SNAPSHOT_HISTORY,
        }),
        auth_kind="api_token",
        docs_url="https://geotab.github.io/sdk/",
        icon="Plug",
        status=ProviderStatus.COMING_SOON,
    ),

    "datatruck": ProviderCatalogEntry(
        provider_id="datatruck",
        display_name="Datatruck",
        tagline="TMS — drivers, trucks, loads, work orders",
        description=(
            "Datatruck is a Transportation Management System (TMS) — "
            "we pull authoritative drivers, trucks, trailers, orders "
            "and work orders FROM Datatruck into 4truck so operators "
            "don't double-enter.  Different from Samsara which provides "
            "LIVE telematics; the two complement each other and can "
            "run side-by-side on the same fleet.  Per-account "
            "credentials: enter your Datatruck company subdomain "
            "(e.g. 'acme' for acme.datatruck.io) plus an API "
            "token minted from Datatruck Settings → API tokens with "
            "Driver, Orders, and Work Order Read scopes."
        ),
        capabilities=frozenset({
            Capability.TMS_DRIVERS_SYNC,
            Capability.TMS_TRUCKS_SYNC,
            Capability.TMS_TRAILERS_SYNC,
            Capability.TMS_ORDERS_SYNC,
            Capability.TMS_WORK_ORDERS_SYNC,
        }),
        # Custom shape — the form must collect a company subdomain
        # in addition to the token because Datatruck's API host is
        # per-tenant (https://{subdomain}.datatruck.io/api/...).
        auth_kind="api_token_with_subdomain",
        docs_url="https://apidocs.datatruck.io/api-reference/introduction",
        icon="Plug",
        status=ProviderStatus.AVAILABLE,
        kind=ProviderKind.TMS,
        feature_defaults={
            # Conservative cadences — Datatruck rate-limits at
            # 20 req/min globally per token.  A driver/truck sync
            # at 15 minutes is plenty fresh for HR + fleet uses,
            # and leaves headroom for orders + work-order pulls.
            #
            # NOT YET HONOURED BY A SCHEDULER.  These intervals are the
            # intended auto-sync cadence, but today the only runner is
            # the manual "Sync now" button (the dashboard shows "manual"
            # next to each toggle, not the interval).  Option A — wire an
            # APScheduler job that calls
            # capabilities.integrations.datatruck.sync.sync_resource per
            # enabled capability on these cadences — turns the intervals
            # real; at that point drop the "manual" relabel in the
            # dashboard's FeatureToggleList.
            Capability.TMS_DRIVERS_SYNC:     {"enabled": True,  "interval_min": 15},
            Capability.TMS_TRUCKS_SYNC:      {"enabled": True,  "interval_min": 15},
            Capability.TMS_TRAILERS_SYNC:    {"enabled": False, "interval_min": 60},
            Capability.TMS_ORDERS_SYNC:      {"enabled": False, "interval_min": 30},
            Capability.TMS_WORK_ORDERS_SYNC: {"enabled": False, "interval_min": 30},
        },
    ),
}
