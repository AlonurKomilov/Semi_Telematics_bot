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
