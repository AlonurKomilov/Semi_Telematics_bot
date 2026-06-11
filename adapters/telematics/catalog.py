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

    feature_defaults: dict[str, dict] = field(default_factory=dict)
    """Default per-capability config (cadence, etc.) applied when an
    owner first connects this provider.  Stored verbatim in
    account_integrations.feature_toggles; owners can override later."""


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
    Capability.STATE_SNAPSHOT_HISTORY:  {"enabled": True, "interval_min": 5},
    Capability.TELEMETRY_HOURLY:        {"enabled": True, "cron": "5 * * * *"},
    Capability.METRICS_DAILY:           {"enabled": True, "cron": "5 0 * * *"},
    Capability.HISTORY_PRUNE:           {"enabled": True, "cron": "0 2 * * *"},
    # ON by default so a fresh connect automatically backfills 30
    # days of Samsara history.  Without this the calendar projection
    # would sit empty for ~7 days while the live aggregators caught
    # up — owners would think the system was broken.  The throttle
    # (1 RPS) and global serial-backfill lock cap the upstream load
    # regardless of how many accounts connect at once.
    Capability.HISTORY_BACKFILL:        {"enabled": True},
}


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
            "(e.g. 'premier' for premier.datatruck.io) plus an API "
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
        feature_defaults={
            # Conservative cadences — Datatruck rate-limits at
            # 20 req/min globally per token.  A driver/truck sync
            # at 15 minutes is plenty fresh for HR + fleet uses,
            # and leaves headroom for orders + work-order pulls.
            Capability.TMS_DRIVERS_SYNC:     {"enabled": True,  "interval_min": 15},
            Capability.TMS_TRUCKS_SYNC:      {"enabled": True,  "interval_min": 15},
            Capability.TMS_TRAILERS_SYNC:    {"enabled": False, "interval_min": 60},
            Capability.TMS_ORDERS_SYNC:      {"enabled": False, "interval_min": 30},
            Capability.TMS_WORK_ORDERS_SYNC: {"enabled": False, "interval_min": 30},
        },
    ),
}
