"""
Samsara API Client — async wrapper for vehicle telematics data.

Supports multi-company: each SamsaraClient wraps one company's API key.
MultiCompanyClient orchestrates parallel queries across all companies.

v3 — Database-driven: company display names and API keys come from the
     database layer, not from hardcoded dicts or env vars.
     Use `build_multi_company_client()` to create clients from DB Company objects.
"""

import asyncio
import copy
import re
import aiohttp
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from constants import METERS_PER_MILE

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class SamsaraPermissionError(Exception):
    """Raised when the API token lacks required permissions."""
    pass


# ── Legacy compat shim — populated at runtime by bot.py ──────────
# reports/ and formatters/ packages still read COMPANY_DISPLAY.
# bot.py calls `populate_company_display(companies)` for display names.
COMPANY_DISPLAY: dict[str, str] = {}

# company_code → Samsara org ID (populated lazily at first alert check)
ORG_IDS: dict[str, str] = {}


def populate_company_display(companies: list) -> dict[str, str]:
    """Populate per-request company display names from database Company objects.

    Updates only the per-request ContextVar so formatters/reports
    pick up the correct tenant-scoped display names.
    Does NOT write to the module-level global to prevent cross-tenant leaks.

    Returns the current display dict for callers that want a local reference.
    """
    from infra.context import set_tenant_display
    # Build a tenant-scoped dict
    display = {}
    for co in companies:
        display[co.code] = co.display_name or co.code
    set_tenant_display(display)
    return display


# ── Samsara Dashboard URL helpers ────────────────────────────────

# Page slug per alert type
_ALERT_PAGE: dict[str, str] = {
    "fault": "diagnostics",
    "health": "diagnostics",
    "fuel": "vehicle-details",
    "events": "safety",
}


def samsara_vehicle_url(
    org_id: str,
    vehicle_id: str,
    alert_type: str = "fault",
    dashboard_base: str = "",
) -> str | None:
    """Build a Samsara Cloud deep-link for a vehicle page.

    Returns None when *org_id* is unavailable (button should be omitted).
    """
    if not org_id:
        return None
    if not dashboard_base:
        from infra.config import SAMSARA_DASHBOARD_URL
        dashboard_base = SAMSARA_DASHBOARD_URL
    return f"{dashboard_base}/o/{org_id}/devices/{vehicle_id}/vehicle"


def samsara_event_url(
    org_id: str,
    event_id: str,
    vehicle_name: str = "",
    dashboard_base: str = "",
) -> str | None:
    """Build a Samsara Cloud deep-link for a safety event.

    Samsara doesn't expose a per-event detail page at a stable URL —
    the previous ``/fleet/reports/safety/event/{id}`` pattern 404s.
    The working pattern lands on the safety inbox filtered to the
    specific event via the ``deviceId`` + ``eventMs`` query params
    (which Samsara's own UI generates from event clicks).

    ``event_id`` is shaped ``"{deviceId}-{timestampMs}"`` (see
    ``get_events``); split it to populate the query params.  When
    *vehicle_name* is provided, add ``q=<name>`` so the inbox is
    pre-filtered to that vehicle even if Samsara doesn't focus the
    exact event row.

    Returns None when *org_id* is empty, *event_id* is empty, or the
    ``device-ms`` split fails (caller should omit the button).
    """
    if not org_id or not event_id:
        return None
    try:
        device_id, event_ms = event_id.rsplit("-", 1)
        int(event_ms)  # validate ms parses; cheap throw if it doesn't
    except (ValueError, AttributeError):
        return None
    if not dashboard_base:
        from infra.config import SAMSARA_DASHBOARD_URL
        dashboard_base = SAMSARA_DASHBOARD_URL

    from urllib.parse import urlencode
    params: dict[str, str] = {
        "deviceId": device_id,
        "eventMs": event_ms,
    }
    if vehicle_name:
        params["q"] = vehicle_name
    return (
        f"{dashboard_base}/o/{org_id}/fleet/reports/safety/inbox_feed"
        f"?{urlencode(params)}"
    )


def samsara_fault_url(
    org_id: str,
    vehicle_id: str,
    dashboard_base: str = "",
) -> str | None:
    """Build a Samsara Cloud deep-link for a vehicle's diagnostics page.

    Lands on Samsara's **Maintenance Status** view for the vehicle —
    the same page that lists Active Faults (SPN / FMI / count) and
    Tire Faults.  This matches what the dispatcher actually wants
    when they tap an Engine Fault alert: see the live DTC list, not
    the generic vehicle profile page our previous
    ``/devices/{id}/vehicle`` URL produced.

    The ``?type=default`` query param is Samsara's own tab selector
    (Summary tab); without it the page can land on a sub-tab the
    user didn't intend.  The account-specific ``tags[]`` filter from
    Samsara's own URL is intentionally omitted — that's UI state for
    one operator and would confuse anyone else opening the link.
    """
    if not org_id:
        return None
    if not dashboard_base:
        from infra.config import SAMSARA_DASHBOARD_URL
        dashboard_base = SAMSARA_DASHBOARD_URL
    return (
        f"{dashboard_base}/o/{org_id}/fleet/maintenance/status/{vehicle_id}"
        f"?type=default"
    )


# Names that indicate ghost / deactivated records (case-insensitive)
_SKIP_NAME_RE = re.compile(
    r"^(deactivated|gpuj-)",
    re.IGNORECASE,
)


class SamsaraClient:
    """Async client for the Samsara REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.samsara.com",
        active_days: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.active_days = active_days   # 0 = no filter
        self._session: Optional[aiohttp.ClientSession] = None
        self._org_id: Optional[str] = None
        self._session_lock = asyncio.Lock()
        self._org_id_lock = asyncio.Lock()
        # Stat types this company's Samsara plan rejected with 400 — skip
        # them on subsequent calls so we don't replay the same error every
        # poll cycle.  TTL=24h re-validates after a plan change.
        # Why a TTL: a tenant upgrading their Samsara plan should pick up
        # the new types automatically without a service restart.
        self._unsupported_stat_types: TTLCache[str, bool] = TTLCache(
            maxsize=64, ttl=24 * 60 * 60,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        # Aggressive per-call timeout: Samsara responds in <1s when
        # healthy; anything past 5s is functionally an outage.  Letting
        # individual requests sit for 30s starved the gunicorn worker
        # pool during outages and cascaded into hangs on pure-internal
        # endpoints (maintenance/work-orders/etc.) that don't even
        # touch Samsara — see ``capabilities/samsara/circuit_breaker.py``
        # for the broader resilience story.
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_org_id(self) -> str | None:
        """Fetch and cache the Samsara organization ID via /me endpoint."""
        if self._org_id is not None:
            return self._org_id
        async with self._org_id_lock:
            if self._org_id is not None:
                return self._org_id
            try:
                data = await self._get("/me")
                self._org_id = str(data.get("data", {}).get("id", ""))
                return self._org_id or None
            except Exception as e:
                logger.warning(f"Failed to fetch Samsara org ID: {e}")
                return None

    def vehicle_url(self, vehicle_id: str) -> str | None:
        """Build a Samsara dashboard URL for a vehicle.

        Returns None if org_id hasn't been fetched yet.
        """
        if not self._org_id or not vehicle_id:
            return None
        return f"https://cloud.samsara.com/o/{self._org_id}/devices/{vehicle_id}/vehicle"

    async def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        # Every Samsara GET goes through the global circuit breaker.
        # When the breaker is open (5 consecutive failures in 60s),
        # this raises ``SamsaraUnavailable`` instantly with no HTTP
        # attempt — preserving gunicorn workers for fast-path internal
        # endpoints while Samsara is dark.
        from adapters.samsara.circuit_breaker import samsara_breaker
        return await samsara_breaker.call(self._get_raw, endpoint, params)

    async def _get_raw(self, endpoint: str, params: Optional[dict] = None) -> dict:
        # observability — record latency + status per endpoint.
        # ``endpoint`` is a static path (~30 distinct values) so the
        # Prometheus label cardinality stays bounded.
        import time as _time
        from infra import observability as _obs

        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        _t0 = _time.perf_counter()
        status_label = "ok"
        try:
            async with session.get(url, params=params) as resp:
                if resp.status in (401, 403):
                    try:
                        body = await resp.json()
                        msg = body.get("message", "Permission denied")
                    except Exception:
                        msg = f"HTTP {resp.status} — permission denied"
                    status_label = "denied"
                    raise SamsaraPermissionError(msg)
                if resp.status >= 500:
                    status_label = "5xx"
                resp.raise_for_status()
                return await resp.json()
        except SamsaraPermissionError:
            raise
        except aiohttp.ClientError as e:
            status_label = "timeout" if "timeout" in str(e).lower() else "error"
            logger.error(f"Samsara API error on {endpoint}: {e}")
            raise
        finally:
            _obs.record_samsara(
                endpoint=endpoint,
                status=status_label,
                seconds=_time.perf_counter() - _t0,
            )

    # ── Vehicle List ─────────────────────────────────────────────

    async def get_vehicles(self) -> list[dict]:
        """Return list of all vehicles in the fleet."""
        data = await self._get("/fleet/vehicles")
        return data.get("data", [])

    async def get_static_driver_assignments(self) -> dict[str, str]:
        """Return ``{vehicle_name_lower: driver_name}`` for every truck
        with a *static* driver assignment configured in Samsara.

        The driver-efficiency endpoint only surfaces drivers who had
        actual drive activity in the requested window — assignments
        that exist in Samsara but had no recent drive time slip through
        the cracks.  Asking ``/fleet/vehicles?decorations=staticAssignedDriver``
        returns the current assignment regardless of activity.

        Returns an empty dict if the API rejects the decoration param
        (older accounts), or on any other error — callers treat that
        as "no static-assignment data available."
        """
        try:
            data = await self._get(
                "/fleet/vehicles",
                params={"decorations": "staticAssignedDriver"},
            )
        except Exception as e:
            logger.debug("static_driver_assignments fetch failed: %s", e)
            return {}
        out: dict[str, str] = {}
        for v in data.get("data", []) or []:
            name = (v.get("name") or "").strip().lower()
            sad = v.get("staticAssignedDriver") or {}
            drv_name = (sad.get("name") or "").strip()
            if name and drv_name:
                out[name] = drv_name
        return out

    # ── Fault Codes ──────────────────────────────────────────────

    async def get_fault_codes(self) -> list[dict]:
        """Return fault code stats for all vehicles."""
        data = await self._get("/fleet/vehicles/stats", params={"types": "faultCodes"})
        return data.get("data", [])

    # ── GPS / Locations ──────────────────────────────────────────

    async def get_locations(self) -> list[dict]:
        """Return latest GPS location for all vehicles."""
        data = await self._get("/fleet/vehicles/locations")
        return data.get("data", [])

    async def get_gps_stats(self) -> list[dict]:
        """Return GPS stats (lat/lng/speed/address) for all vehicles."""
        data = await self._get("/fleet/vehicles/stats", params={"types": "gps"})
        return data.get("data", [])

    async def get_engine_states(self) -> list[dict]:
        """Return latest engine state (On/Off/Idle) for all vehicles.

        Uses /fleet/vehicles/stats?types=engineStates which returns the
        most recent engine state per vehicle.  If this company's plan
        doesn't support engineStates we cache that fact for 24h so we
        don't replay the same 400 every map-poll cycle.
        """
        if "engineStates" in self._unsupported_stat_types:
            return []
        try:
            data = await self._get(
                "/fleet/vehicles/stats", params={"types": "engineStates"},
            )
            return data.get("data", [])
        except aiohttp.ClientResponseError as e:
            if e.status == 400:
                self._unsupported_stat_types["engineStates"] = True
                logger.warning(
                    "engineStates not supported by this Samsara plan — "
                    "caching as unsupported for 24h",
                )
            else:
                logger.warning("Engine states unavailable (non-fatal): %s", e)
            return []
        except Exception as e:
            logger.warning("Engine states unavailable (non-fatal): %s", e)
            return []

    async def get_vehicle_location(
        self, vehicle_id: str, company: str | None = None,
    ) -> dict | None:
        """Get fresh GPS location for a single vehicle.

        Uses /fleet/vehicles/stats?types=gps&vehicleIds=<id> to get
        the latest GPS reading for one specific vehicle.
        Returns the location dict or None.
        """
        try:
            data = await self._get(
                "/fleet/vehicles/stats",
                params={"types": "gps", "vehicleIds": vehicle_id},
            )
            items = data.get("data", [])
            if items:
                gps = items[0].get("gps", {})
                return gps if isinstance(gps, dict) else None
        except Exception as e:
            logger.debug("get_vehicle_location(%s) failed: %s", vehicle_id, e)
        return None

    # ── Fuel Levels ──────────────────────────────────────────────

    async def get_fuel_levels(self) -> list[dict]:
        """Return fuel percent and DEF level for all vehicles.

        Uses _safe_stats() which retries each type individually if the
        combined request fails — so a company whose gateway does not
        report DEF will still return fuel data (DEF will be absent from
        those vehicle objects rather than crashing the whole call).
        """
        try:
            result = await self._safe_stats("fuelPercents,defLevelMilliPercent")
            return result.get("data", [])
        except Exception as e:
            logger.warning(f"Fuel/DEF stats unavailable (non-fatal): {e}")
            return []

    async def get_current_odometer_readings(self) -> list[dict]:
        """Return current odometer readings (in miles) for all vehicles.

        Calls /fleet/vehicles/stats?types=obdOdometerMeters which returns
        the most recent odometer value per vehicle.
        Returns a list of dicts: {id, name, odometer_miles, time}
        """
        try:
            data = await self._get("/fleet/vehicles/stats",
                                   params={"types": "obdOdometerMeters"})
            results = []
            for v in data.get("data", []):
                odo = v.get("obdOdometerMeters", {})
                if isinstance(odo, dict) and odo.get("value") is not None:
                    # Samsara returns meters; convert to miles
                    results.append({
                        "id": v.get("id", ""),
                        "name": v.get("name", ""),
                        "odometer_miles": round(odo["value"] / 1609.344, 1),
                        "time": odo.get("time", ""),
                    })
            return results
        except Exception as e:
            logger.warning(f"Odometer stats unavailable (non-fatal): {e}")
            return []

    async def get_current_engine_hours_readings(self) -> list[dict]:
        """Return cumulative engine hours per vehicle — parallel to
        ``get_current_odometer_readings``.

        Calls /fleet/vehicles/stats?types=obdEngineSeconds which returns
        the OBD-reported lifetime engine-on counter.  Some Samsara plans
        / vehicles without the engine-hours signal return no value; those
        vehicles simply drop out of the result.

        Returns dicts: {id, name, engine_hours, time}.
        """
        try:
            data = await self._get(
                "/fleet/vehicles/stats",
                params={"types": "obdEngineSeconds"},
            )
            results: list[dict] = []
            for v in data.get("data", []):
                eng = v.get("obdEngineSeconds", {})
                if isinstance(eng, dict) and eng.get("value") is not None:
                    seconds = eng["value"]
                    results.append({
                        "id": v.get("id", ""),
                        "name": v.get("name", ""),
                        "engine_hours": round(float(seconds) / 3600.0, 1),
                        "time": eng.get("time", ""),
                    })
            return results
        except Exception as e:
            logger.warning(f"Engine-hours stats unavailable (non-fatal): {e}")
            return []

    # ── Drivers ──────────────────────────────────────────────────

    async def get_drivers(self) -> list[dict]:
        """Return list of all fleet drivers."""
        data = await self._get("/fleet/drivers")
        return data.get("data", [])

    # ── Geofences ────────────────────────────────────────────────

    async def get_geofences(self) -> list[dict]:
        """Return all geofences defined in the Samsara dashboard."""
        data = await self._get("/fleet/geofences")
        return data.get("data", [])

    # ── Safety Events ────────────────────────────────────────────

    async def get_events(self, days: int = 7) -> list[dict]:
        """Get safety events (harsh brake, crash, etc.) for the time range.

        Uses cursor pagination. Returns normalized event dicts sorted by
        time descending (newest first).
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        params: dict = {
            "startTime": start.isoformat(),
            "endTime": now.isoformat(),
        }

        all_events: list[dict] = []
        try:
            for _ in range(200):  # safety limit
                resp = await self._get("/fleet/safety-events", params=params)
                for evt in resp.get("data", []):
                    labels = evt.get("behaviorLabels", [])
                    if not labels:
                        continue
                    driver = evt.get("driver") or {}
                    vehicle = evt.get("vehicle") or {}
                    loc = evt.get("location") or {}
                    ext_ids = vehicle.get("externalIds") or {}
                    all_events.append({
                        "event_id": evt.get("id", ""),
                        "event_type": labels[0].get("label", "unknown"),
                        "event_name": labels[0].get("name", "Unknown"),
                        "driver_id": driver.get("id", ""),
                        "driver_name": driver.get("name", "Unassigned"),
                        "vehicle_id": vehicle.get("id", ""),
                        "vehicle_name": vehicle.get("name", "?"),
                        "vehicle_vin": ext_ids.get("samsara.vin", ""),
                        "time": evt.get("time", ""),
                        "g_force": evt.get("maxAccelerationGForce", 0.0),
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude"),
                        # Reverse-geocoded address when Samsara provides
                        # one — flat alias mirrors the vehicles flow above
                        # so the alert formatter can prefer "City, ST"
                        # over raw coords.
                        "address": (
                            (loc.get("reverseGeo") or {}).get("formattedLocation", "")
                            or ""
                        ),
                        "coaching_state": evt.get("coachingState", ""),
                        "video_url": evt.get("downloadForwardVideoUrl"),
                        "inward_video_url": evt.get("downloadInwardVideoUrl"),
                    })
                pag = resp.get("pagination", {})
                if not pag.get("hasNextPage"):
                    break
                params["after"] = pag.get("endCursor")

        except SamsaraPermissionError:
            logger.info("Events API: token lacks permission — returning []")
            return []

        all_events.sort(key=lambda x: x.get("time", ""), reverse=True)
        return all_events

    async def get_safety_event(self, event_id: str) -> dict | None:
        """Fetch a single safety event with fresh signed video URLs.

        Samsara's ``downloadForwardVideoUrl`` / ``downloadInwardVideoUrl``
        are S3 pre-signed URLs with an 8-hour expiry, so stored URLs
        from the warehouse routinely outlive that window.

        There is no ``GET /fleet/safety-events/{id}`` endpoint — that
        path returns 404.  Workaround: the event_id's second segment
        is a Unix-ms timestamp, so we hit the *list* endpoint with a
        tight time window around that timestamp and find the row by
        id.  Samsara re-signs the URLs on every list call, so the
        returned ``video_url`` is fresh.

        Returns ``None`` if we can't parse the timestamp, the token
        lacks permission, or no event with the requested id falls in
        the window.
        """
        # Parse the embedded timestamp.  Sample id: "281474990100062-1778375403968".
        try:
            ms = int(event_id.rsplit("-", 1)[1])
            ts = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        except (ValueError, IndexError):
            logger.warning("get_safety_event: cannot parse timestamp from %s", event_id)
            return None

        # ±10 min window.  Samsara emits events at ~real time and we
        # only need to catch the single id, so a tight slice keeps the
        # response tiny.
        start = ts - timedelta(minutes=10)
        end = ts + timedelta(minutes=10)
        params: dict = {
            "startTime": start.isoformat(),
            "endTime":   end.isoformat(),
        }

        try:
            resp = await self._get("/fleet/safety-events", params=params)
        except SamsaraPermissionError:
            logger.info("Events API: token lacks permission for event %s", event_id)
            return None
        except Exception as e:
            logger.warning("get_safety_event(%s) failed: %s", event_id, e)
            return None

        for evt in (resp or {}).get("data", []) or []:
            if evt.get("id") != event_id:
                continue
            labels = evt.get("behaviorLabels", []) or []
            return {
                "event_id":         evt.get("id", event_id),
                "event_type":       (labels[0].get("label") if labels else "unknown"),
                "event_name":       (labels[0].get("name") if labels else ""),
                "video_url":        evt.get("downloadForwardVideoUrl"),
                "inward_video_url": evt.get("downloadInwardVideoUrl"),
                "vehicle":          evt.get("vehicle") or {},
                "driver":           evt.get("driver") or {},
            }
        return None

    # ── Active-vehicle filter ────────────────────────────────────

    def _is_active(self, vehicle: dict, location: dict) -> bool:
        """Check if a vehicle should be included.

        Filters out:
          1. Ghost names (Deactivated…, GPUJ-…)
          2. Vehicles with no Samsara gateway device
          3. Vehicles with no GPS data
          4. Vehicles whose last GPS ping is older than active_days
             (skip this check when active_days == 0)
        """
        name = vehicle.get("name", "")
        if _SKIP_NAME_RE.search(name):
            return False

        # Samsara dashboard only shows vehicles with a physical gateway
        if not vehicle.get("gateway"):
            return False

        if self.active_days == 0:
            return True

        loc_time = location.get("time", "")
        if not loc_time:
            return False

        try:
            dt = datetime.fromisoformat(loc_time.replace("Z", "+00:00"))
            days_ago = (datetime.now(timezone.utc) - dt).days
            return days_ago <= self.active_days
        except Exception:
            return False

    # ── Combined: Enriched Vehicle Data ──────────────────────────

    async def get_fleet_overview(self) -> list[dict]:
        """
        Build an enriched list of **active** vehicles by merging:
        vehicle info + fault codes + GPS + fuel.

        Vehicles are filtered by GPS recency (active_days) and
        ghost-name patterns.
        """
        vehicles_raw = await self.get_vehicles()
        fault_raw = await self.get_fault_codes()
        location_raw = await self.get_locations()
        fuel_raw = await self.get_fuel_levels()

        # Index by vehicle ID
        vehicles = {v["id"]: v for v in vehicles_raw}
        faults_by_id = {v["id"]: v.get("faultCodes", {}) for v in fault_raw}
        loc_by_id = {v["id"]: v.get("location", {}) for v in location_raw}
        # Samsara may return the key as "fuelPercents" (matching the type name)
        # or "fuelPercent" (singular); read both defensively.
        fuel_by_id = {
            v["id"]: v.get("fuelPercents") or v.get("fuelPercent") or {}
            for v in fuel_raw
        }
        def_by_id: dict[str, dict] = {}
        for fv in fuel_raw:
            # defLevelMilliPercent is absent (not just empty) for vehicles
            # without DEF sensors — treat missing or empty the same way.
            d = fv.get("defLevelMilliPercent") or {}
            val = d.get("value") if isinstance(d, dict) else None
            if val is not None:
                def_by_id[fv["id"]] = {
                    "value": round(val / 1000, 1),
                    "time": d.get("time", ""),
                }

        enriched = []
        skipped = 0
        for vid, v in vehicles.items():
            name = v.get("name", "?")
            loc  = loc_by_id.get(vid, {})

            if not self._is_active(v, loc):
                skipped += 1
                continue

            # Normalise keys: the /fleet/vehicles/locations endpoint
            # uses "speed" (mph) and "reverseGeo.formattedLocation",
            # while /fleet/vehicles/stats?types=gps uses
            # "speedMilesPerHour" and the same reverseGeo layout.
            # Add the stats-style aliases so downstream consumers
            # (parking checks, maps, etc.) can use either name.
            if "speedMilesPerHour" not in loc and "speed" in loc:
                loc["speedMilesPerHour"] = loc["speed"]
            if "address" not in loc:
                loc["address"] = (
                    loc.get("reverseGeo", {}).get("formattedLocation", "")
                    or ""
                )

            fc = faults_by_id.get(vid, {})
            j1939 = fc.get("j1939", {})
            dtcs = j1939.get("diagnosticTroubleCodes", [])
            cel = j1939.get("checkEngineLights", {})

            entry = {
                "id": vid,
                "name": name,
                "vin": v.get("vin", "N/A"),
                "make": v.get("make", "N/A"),
                "model": v.get("model", "N/A"),
                "year": v.get("year", "N/A"),
                "license_plate": v.get("licensePlate", "N/A"),
                "fault_codes": fc,
                "location": loc,
                "fuel": fuel_by_id.get(vid, {}),
                "def_level": def_by_id.get(vid, {}),
            }

            # Pre-parse fault data so downstream code can use
            # _dtcs / _lights without re-digging into fault_codes.
            if dtcs:
                entry["_dtcs"] = dtcs
                entry["_lights"] = cel

            enriched.append(entry)

        if skipped:
            logger.info(f"Filtered out {skipped} inactive vehicles "
                        f"(active_days={self.active_days})")

        # Sort by name (truck number)
        enriched.sort(key=lambda x: x["name"])
        return enriched

    async def get_vehicle_detail(self, vehicle_name: str) -> Optional[dict]:
        """
        Get enriched data for a single vehicle by truck name/number.

        Unlike get_fleet_overview(), this searches ALL vehicles (including
        those without a gateway) so direct truck lookups always work.
        """
        vehicles_raw = await self.get_vehicles()
        fault_raw = await self.get_fault_codes()
        location_raw = await self.get_locations()
        fuel_raw = await self.get_fuel_levels()

        faults_by_id = {v["id"]: v.get("faultCodes", {}) for v in fault_raw}
        loc_by_id = {v["id"]: v.get("location", {}) for v in location_raw}
        fuel_by_id = {
            v["id"]: v.get("fuelPercents") or v.get("fuelPercent") or {}
            for v in fuel_raw
        }
        def_by_id: dict[str, dict] = {}
        for fv in fuel_raw:
            d = fv.get("defLevelMilliPercent") or {}
            val = d.get("value") if isinstance(d, dict) else None
            if val is not None:
                def_by_id[fv["id"]] = {
                    "value": round(val / 1000, 1),
                    "time": d.get("time", ""),
                }

        truck_name_lower = vehicle_name.strip().lower()
        for v in vehicles_raw:
            name = v.get("name", "?")
            if name.lower() != truck_name_lower:
                continue
            if _SKIP_NAME_RE.search(name):
                continue

            vid = v["id"]
            loc = loc_by_id.get(vid, {})
            # Normalise speed / address keys (see get_fleet_overview)
            if "speedMilesPerHour" not in loc and "speed" in loc:
                loc["speedMilesPerHour"] = loc["speed"]
            if "address" not in loc:
                loc["address"] = (
                    loc.get("reverseGeo", {}).get("formattedLocation", "")
                    or ""
                )
            result = {
                "id": vid,
                "name": name,
                "vin": v.get("vin", "N/A"),
                "make": v.get("make", "N/A"),
                "model": v.get("model", "N/A"),
                "year": v.get("year", "N/A"),
                "license_plate": v.get("licensePlate", "N/A"),
                "fault_codes": faults_by_id.get(vid, {}),
                "location": loc,
                "fuel": fuel_by_id.get(vid, {}),
                "def_level": def_by_id.get(vid, {}),
                "has_gateway": bool(v.get("gateway")),
            }
            return result

        return None

    async def get_vehicles_with_faults(self) -> tuple[list[dict], int]:
        """Return (vehicles_with_active_DTCs, total_active_vehicles)."""
        fleet = await self.get_fleet_overview()
        total = len(fleet)
        result = []
        for v in fleet:
            fc = v.get("fault_codes", {})
            j1939 = fc.get("j1939", {})
            dtcs = j1939.get("diagnosticTroubleCodes", [])
            cel = j1939.get("checkEngineLights", {})
            if dtcs:
                v["_dtcs"] = dtcs
                v["_lights"] = cel
                v["_fault_time"] = fc.get("time", "")
                result.append(v)
        return result, total

    async def get_low_fuel_vehicles(self, threshold: int = 20) -> list[dict]:
        """Return vehicles with fuel level below the threshold %."""
        fleet = await self.get_fleet_overview()
        low = []
        for v in fleet:
            fuel = v.get("fuel", {})
            pct = fuel.get("value")
            if pct is not None and pct <= threshold:
                v["_fuel_pct"] = pct
                v["_fuel_time"] = fuel.get("time", "")
                low.append(v)
        low.sort(key=lambda x: x.get("_fuel_pct", 999))
        return low

    # ── Fleet Weather (Ambient Conditions) ───────────────────────

    async def get_fleet_weather(self) -> list[dict]:
        """Return ambient air temperature and barometric pressure for all active vehicles.

        Each vehicle dict gets a ``_weather`` sub-dict with:
        - ``temp_f``: ambient air temperature in °F
        - ``temp_c``: ambient air temperature in °C
        - ``temp_time``: ISO timestamp
        - ``baro_psi``: barometric pressure in PSI
        - ``baro_inhg``: barometric pressure in inHg
        - ``baro_time``: ISO timestamp
        """
        fleet = await self.get_fleet_overview()
        if not fleet:
            return []

        data = await self._get(
            "/fleet/vehicles/stats",
            params={"types": "ambientAirTemperatureMilliC,barometricPressurePa"},
        )
        stats_by_id: dict[str, dict] = {}
        for v in data.get("data", []):
            stats_by_id[v["id"]] = v

        results: list[dict] = []
        for v in fleet:
            s = stats_by_id.get(v["id"], {})
            weather: dict = {}

            temp_obj = s.get("ambientAirTemperatureMilliC", {})
            temp_val = temp_obj.get("value")
            if temp_val is not None:
                temp_c = temp_val / 1000
                weather["temp_c"] = round(temp_c, 1)
                weather["temp_f"] = round(temp_c * 9 / 5 + 32, 1)
                weather["temp_time"] = temp_obj.get("time", "")

            baro_obj = s.get("barometricPressurePa", {})
            baro_val = baro_obj.get("value")
            if baro_val is not None:
                weather["baro_psi"] = round(baro_val * 0.000145038, 1)
                weather["baro_inhg"] = round(baro_val * 0.0002953, 2)
                weather["baro_time"] = baro_obj.get("time", "")

            v["_weather"] = weather
            results.append(v)

        # Sort by temperature ascending (coldest first)
        results.sort(key=lambda x: x.get("_weather", {}).get("temp_f", 999))
        return results

    # ── Vehicle Health Diagnostics ───────────────────────────────

    async def _safe_stats(self, types: str) -> dict:
        """Fetch /fleet/vehicles/stats with per-type fallback on 400.

        If the batched call fails, retries each stat type individually
        so fleets that don't support certain types still get partial data.
        Types this company's plan has already 400'd on are filtered out
        upfront via ``_unsupported_stat_types`` so we don't burn a network
        round-trip per poll cycle on known-bad inputs.
        """
        wanted = [t for t in types.split(",") if t and t not in self._unsupported_stat_types]
        if not wanted:
            return {"data": []}

        try:
            return await self._get(
                "/fleet/vehicles/stats", params={"types": ",".join(wanted)}
            )
        except Exception:
            # Batch failed — try each type individually so a single bad
            # type doesn't poison the rest of the response.
            logger.debug(f"Stats batch [{','.join(wanted)}] failed, trying individually")
            merged: dict[str, dict] = {}
            for t in wanted:
                try:
                    data = await self._get(
                        "/fleet/vehicles/stats", params={"types": t}
                    )
                    for v in data.get("data", []):
                        merged.setdefault(v["id"], {"id": v["id"]}).update(v)
                except aiohttp.ClientResponseError as e:
                    if e.status == 400:
                        self._unsupported_stat_types[t] = True
                        logger.warning(
                            "Stat type %s not supported by this Samsara "
                            "plan — caching as unsupported for 24h", t,
                        )
                    else:
                        logger.debug(f"Stats type {t} failed: {e}")
                except Exception as e:
                    logger.debug(f"Stats type {t} failed: {e}")
            return {"data": list(merged.values())}

    async def get_vehicle_health(self) -> list[dict]:
        """Return health diagnostics for all active vehicles.

        Fetches: DEF level, coolant temp, battery voltage,
        oil pressure, engine load, seatbelt, engine RPM.
        Returns enriched vehicle dicts with _health sub-dict.
        """
        fleet = await self.get_fleet_overview()
        if not fleet:
            return []

        # Samsara limits /fleet/vehicles/stats to 4 types per call
        batch1 = "defLevelMilliPercent,engineCoolantTemperatureMilliC,batteryMilliVolts,engineOilPressureKPa"
        batch2 = "engineLoadPercent,seatbeltDriver,engineRpm"

        data1, data2 = await asyncio.gather(
            self._safe_stats(batch1),
            self._safe_stats(batch2),
        )

        stats_by_id: dict[str, dict] = {}
        for v in data1.get("data", []):
            stats_by_id[v["id"]] = v
        for v in data2.get("data", []):
            stats_by_id.setdefault(v["id"], {}).update(v)

        results: list[dict] = []
        for v in fleet:
            s = stats_by_id.get(v["id"], {})
            health: dict = {}

            def _val(key):
                d = s.get(key, {})
                return d.get("value"), d.get("time", "")

            # DEF level (milli-percent → percent)
            def_val, def_time = _val("defLevelMilliPercent")
            if def_val is not None:
                health["def_pct"] = round(def_val / 1000, 1)
                health["def_time"] = def_time

            # Coolant temp (milli-C → °C)
            cool_val, cool_time = _val("engineCoolantTemperatureMilliC")
            if cool_val is not None:
                health["coolant_c"] = round(cool_val / 1000, 1)
                health["coolant_time"] = cool_time

            # Battery (milli-volts → volts)
            batt_val, batt_time = _val("batteryMilliVolts")
            if batt_val is not None:
                health["battery_v"] = round(batt_val / 1000, 2)
                health["battery_time"] = batt_time

            # Oil pressure (kPa → psi)
            oil_val, oil_time = _val("engineOilPressureKPa")
            if oil_val is not None:
                health["oil_psi"] = round(oil_val * 0.145038, 1)
                health["oil_kpa"] = oil_val
                health["oil_time"] = oil_time

            # Engine load %
            load_val, load_time = _val("engineLoadPercent")
            if load_val is not None:
                health["load_pct"] = load_val
                health["load_time"] = load_time

            # Seatbelt
            seat_val, seat_time = _val("seatbeltDriver")
            if seat_val is not None:
                health["seatbelt"] = seat_val  # "Buckled" or "Unbuckled"
                health["seatbelt_time"] = seat_time

            # Engine RPM
            rpm_val, rpm_time = _val("engineRpm")
            if rpm_val is not None:
                health["rpm"] = rpm_val
                health["rpm_time"] = rpm_time

            # Engine state — multi-signal detection with fallback chain:
            #   1. engineRpm > 0 (CAN bus, most reliable)
            #   2. engineLoadPercent > 0 (CAN bus, secondary)
            #   3. batteryMilliVolts > 13200 (alternator charging = engine on)
            #   4. GPS speed > 3 km/h (moving = engine on)
            #   5. None — unknown
            # PTG gateways and some older units don't report RPM, so the
            # additional fallback signals prevent false health alerts for
            # companies without full CAN bus support.
            #
            # Staleness: if the most-trusted signal is >15 min old, the
            # truck likely turned off since that reading → treat engine_on
            # as False to avoid alerting on stale data.
            _STALE_MINUTES = 15
            _stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_MINUTES)

            def _is_fresh(ts: str) -> bool:
                """Return True if the ISO timestamp is within the staleness window."""
                if not ts:
                    return False
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    return dt >= _stale_cutoff
                except (ValueError, TypeError):
                    return False

            def _parse_ts(ts: str):
                """Parse ISO timestamp to datetime, or None."""
                if not ts:
                    return None
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None

            def _timestamps_close(ts1: str, ts2: str, max_gap_seconds: int = 180) -> bool:
                """Return True if two timestamps are within max_gap_seconds of each other.

                This prevents false alerts when the engine-on signal (RPM)
                and a health reading (oil/battery/coolant) are from different
                engine states — e.g. RPM is fresh (engine just started) but
                oil pressure is from minutes ago (post-shutdown, naturally low).
                """
                dt1, dt2 = _parse_ts(ts1), _parse_ts(ts2)
                if dt1 is None or dt2 is None:
                    return False
                return abs((dt1 - dt2).total_seconds()) <= max_gap_seconds

            loc_speed = v.get("location", {}).get("speed")  # km/h from GPS
            load_val_raw = health.get("load_pct")
            engine_on_time = None  # timestamp of the signal that determined engine_on
            if rpm_val is not None and _is_fresh(rpm_time):
                engine_on = rpm_val > 0
                engine_on_time = rpm_time
            elif load_val_raw is not None and _is_fresh(load_time):
                engine_on = load_val_raw > 0
                engine_on_time = load_time
            elif batt_val is not None and batt_val > 13200 and _is_fresh(batt_time):
                # >13.2V means alternator is charging = engine running
                engine_on = True
                engine_on_time = batt_time
            elif loc_speed is not None:
                engine_on = loc_speed > 3  # >3 km/h ≈ moving = engine on
                engine_on_time = None  # GPS doesn't give a comparable timestamp
            else:
                engine_on = None  # unknown
            health["engine_on"] = engine_on

            # Count alerts — only flag pressure/voltage/temp when engine is
            # confirmed running AND the reading is fresh AND the reading
            # timestamp is close to the engine-on signal.
            #
            # The timestamp-closeness check (_timestamps_close) is critical:
            # Samsara CAN bus sensors report at different frequencies. RPM
            # may update every few seconds while oil pressure updates every
            # 2-5 minutes. Without this check, a stale low oil pressure
            # reading from post-shutdown (normal) can trigger a false alert
            # when RPM reports the engine just restarted.
            alerts = []
            if health.get("battery_v") is not None and health["battery_v"] < 12.2:
                if engine_on and _is_fresh(health.get("battery_time", "")) \
                        and _timestamps_close(health.get("battery_time", ""), engine_on_time or ""):
                    alerts.append("low_battery")
            if health.get("oil_psi") is not None and health["oil_psi"] < 10:
                if engine_on and _is_fresh(health.get("oil_time", "")) \
                        and _timestamps_close(health.get("oil_time", ""), engine_on_time or ""):
                    alerts.append("low_oil_pressure")
            if health.get("coolant_c") is not None and health["coolant_c"] > 105:
                if engine_on and _is_fresh(health.get("coolant_time", "")) \
                        and _timestamps_close(health.get("coolant_time", ""), engine_on_time or ""):
                    alerts.append("high_coolant_temp")
            if health.get("def_pct") is not None and health["def_pct"] < 10:
                alerts.append("low_def")
            if health.get("seatbelt") == "Unbuckled":
                alerts.append("seatbelt_unbuckled")

            v["_health"] = health
            v["_health_alerts"] = alerts
            results.append(v)

        # Sort: vehicles with alerts first, then by name
        results.sort(key=lambda x: (-len(x["_health_alerts"]), x["name"]))
        return results

    # ── Driver Efficiency ────────────────────────────────────────

    @staticmethod
    def _summarize_efficiency(s: dict) -> dict:
        """Distil one Samsara efficiency record into our internal shape.

        Accepts EITHER a driver-level ``driverSummaries[i]`` (keys prefixed
        ``total*``) or a per-vehicle ``vehicleSummaries[i]`` (no prefix), so
        the same conversions live in one place — fixes drift bugs where the
        driver and vehicle merges grew slightly different fields.

        All time conversions and unit conversions happen here; callers just
        merge the result.  Percentages are rounded to one decimal **once**
        — callers (route, CSV, PDF) are free to re-round but won't lose
        precision the way the previous double-rounding did.
        """
        dist_m       = s.get("totalDistanceDrivenMeters")  or s.get("distanceDrivenMeters", 0)
        drive_ms     = s.get("totalDriveTimeDurationMs")   or s.get("driveTimeDurationMs", 0)
        idle_ms      = s.get("totalIdleTimeDurationMs", 0)
        fuel_ml      = s.get("totalFuelConsumedMl")        or s.get("fuelConsumedMl", 0)
        green_ms     = s.get("greenBandDrivingDurationMs", 0)
        coast_ms     = s.get("coastingDurationMs", 0)
        cruise_ms    = s.get("cruiseControlDurationMs", 0)
        antic_brk    = s.get("anticipationBrakeEventCount", 0)
        total_brk    = s.get("totalBrakeEventCount", 0)
        hi_torque_ms = s.get("highTorqueMs", 0)
        overspeed_ms = s.get("overSpeedMs", 0)

        miles    = dist_m / METERS_PER_MILE
        fuel_gal = fuel_ml / 3785.41
        mpg      = miles / fuel_gal if fuel_gal > 0 else 0
        return {
            "_miles":         round(miles, 1),
            "_drive_h":       round(drive_ms / 3600000, 1),
            "_idle_h":        round(idle_ms / 3600000, 1),
            "_fuel_gal":      round(fuel_gal, 1),
            "_mpg":           round(mpg, 1),
            "_green_pct":     round(green_ms / drive_ms * 100, 1) if drive_ms > 0 else 0,
            "_coast_min":     round(coast_ms / 60000, 1),
            "_cruise_min":    round(cruise_ms / 60000, 1),
            "_overspeed_min": round(overspeed_ms / 60000, 1),
            "_hi_torque_min": round(hi_torque_ms / 60000, 1),
            "_antic_brakes":  antic_brk,
            "_total_brakes":  total_brk,
            "_antic_pct":     round(antic_brk / total_brk * 100, 1) if total_brk > 0 else 0,
            # Internal: caller decides whether to drop zero-activity rows
            "_total_active_ms": drive_ms + idle_ms,
        }

    async def get_driver_efficiency(self, days: int = 7) -> list[dict]:
        """Get driver efficiency data for the specified time range.

        Returns list of driver dicts with efficiency metrics:
        fuel consumed, idle time, over-speed, braking, eco-driving, etc.
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        data = await self._get("/fleet/drivers/efficiency", params={
            "startTime": start.isoformat(),
            "endTime": now.isoformat(),
        })

        summaries = (data.get("data", {}) or {}).get("driverSummaries", [])
        results: list[dict] = []

        for s in summaries:
            summary = self._summarize_efficiency(s)
            total_ms = summary.pop("_total_active_ms")
            if total_ms <= 0:
                continue

            drive_ms  = s.get("totalDriveTimeDurationMs", 0)
            drive_pct = round(drive_ms / total_ms * 100)
            idle_pct  = 100 - drive_pct

            driver = s.get("driver", {})
            results.append({
                "driver_id":          driver.get("id", ""),
                "driver_name":        driver.get("name", "?"),
                "_drive_pct":         drive_pct,
                "_idle_pct":          idle_pct,
                **summary,
                "_vehicle_summaries": s.get("vehicleSummaries", []),
            })

        results.sort(key=lambda x: x["driver_name"])
        return results

    # ── Fleet Efficiency (merged engine hours + driver efficiency) ─

    async def get_fleet_efficiency(self, days: int = 7) -> list[dict]:
        """Merge engine hours (per-truck) with driver efficiency (per-driver).

        Calls both APIs in parallel, then matches driver data to trucks
        via ``vehicleSummaries[].vehicle.name``.

        Every truck gets engine hours data (100% coverage).
        Trucks with a registered driver also get fuel/MPG/eco/overspeed.

        Returns list of truck dicts sorted by name, each containing:
            name, id, _org,
            _engine_hours, _driving_hours, _idle_hours, _driving_pct,
            _idle_pct, _miles, _engine_s, _driving_s, _idle_s,
            _driver_name (str|None), _fuel_gal, _mpg, _green_pct,
            _overspeed_min, _antic_brakes, _total_brakes, _antic_pct
        """
        # Fetch all three in parallel — engine hours (per-truck),
        # driver efficiency (per-driver, activity-based), and Samsara's
        # static driver→vehicle assignment.  The third source backfills
        # the driver name for trucks that are assigned in Samsara but
        # didn't have drive activity in the window (which would otherwise
        # show as "no driver paired" on the dashboard).
        eng_task    = asyncio.create_task(self.get_engine_hours(days))
        static_task = asyncio.create_task(self.get_static_driver_assignments())
        try:
            drv_task = asyncio.create_task(self.get_driver_efficiency(days))
            vehicles, drivers, static_assignments = await asyncio.gather(
                eng_task, drv_task, static_task,
            )
        except Exception:
            # Efficiency API may fail (license issue) — engine hours alone.
            vehicles = await eng_task
            drivers = []
            try:
                static_assignments = await static_task
            except Exception:
                static_assignments = {}

        # Build lookup: vehicle_name (lowercase) → driver efficiency per-vehicle
        driver_by_truck: dict[str, dict] = {}
        for drv in drivers:
            for vs in drv.get("_vehicle_summaries", []):
                veh = vs.get("vehicle", {})
                vname = (veh.get("name") or "").strip().lower()
                if not vname:
                    continue
                summary = self._summarize_efficiency(vs)
                summary.pop("_total_active_ms", None)
                # Drop fields that don't apply at the per-vehicle level
                for k in ("_drive_h", "_idle_h"):
                    summary.pop(k, None)
                summary["_driver_name"] = drv["driver_name"]
                driver_by_truck[vname] = summary

        # Merge driver data into truck records
        for v in vehicles:
            truck_key = v["name"].strip().lower()
            drv_data = driver_by_truck.get(truck_key)
            if drv_data:
                v.update(drv_data)
            else:
                # No activity-based pairing — fall back to Samsara's
                # static assignment so dashboards still show the driver
                # who's registered as "this truck's driver."  All the
                # efficiency-derived fields stay None because we have
                # no drive activity to report on.
                v["_driver_name"]    = static_assignments.get(truck_key)
                v["_fuel_gal"]       = None
                v["_mpg"]            = None
                v["_green_pct"]      = None
                v["_overspeed_min"]  = None
                v["_coast_min"]      = None
                v["_cruise_min"]     = None
                v["_hi_torque_min"]  = None
                v["_miles"]          = None
                v["_antic_brakes"]   = None
                v["_total_brakes"]   = None
                v["_antic_pct"]      = None

        vehicles.sort(key=lambda x: x["name"])
        return vehicles

    # ── Engine Hours + Idle Analysis ─────────────────────────────

    async def _get_paginated_history(
        self, types: str, start: datetime, end: datetime,
    ) -> dict[str, dict]:
        """Fetch stats/history with full cursor pagination.

        Returns ``{vehicle_id: {"name": str, **per_type_lists}}``
        where per-type keys map to lists of data-point dicts.

        Example types: ``"obdEngineSeconds,obdOdometerMeters"``
        """
        type_keys = [t.strip() for t in types.split(",")]
        vehicles: dict[str, dict] = {}
        cursor: str | None = None

        for _ in range(200):  # safety limit
            params: dict = {
                "types": types,
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
            }
            if cursor:
                params["after"] = cursor

            resp = await self._get(
                "/fleet/vehicles/stats/history", params=params,
            )
            for v in resp.get("data", []):
                vid = v.get("id", "")
                if vid not in vehicles:
                    vehicles[vid] = {"name": v.get("name", "?")}
                    for k in type_keys:
                        vehicles[vid][k] = []
                for k in type_keys:
                    vehicles[vid][k].extend(v.get(k, []))

            pag = resp.get("pagination", {})
            if not pag.get("hasNextPage"):
                break
            cursor = pag.get("endCursor")

        return vehicles

    async def get_engine_hours(self, days: int = 7) -> list[dict]:
        """Get weekly engine hours with driving / idle breakdown.

        Primary: ``engineStates`` (CAN bus) for driving / idle / off,
        combined with ``obdOdometerMeters`` for miles.

        Fallback: if the company does not support ``engineStates`` (some
        Samsara plans return HTTP 400), uses the OBD-only approach
        with ``obdEngineSeconds`` + ``obdOdometerMeters``.

        Returns a list of dicts sorted by name:
            name, id, _engine_hours, _driving_hours, _idle_hours,
            _driving_pct, _idle_pct, _miles,
            _engine_s, _driving_s, _idle_s
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        # Try engineStates first (preferred — native CAN bus).
        try:
            states_raw = await self._get_paginated_history(
                "engineStates", start, end=now,
            )
        except aiohttp.ClientError:
            # Some Samsara plans don't support engineStates (HTTP 400).
            states_raw = {}

        if states_raw:
            return await self._engine_hours_from_states(
                states_raw, start, now,
            )
        # Fallback to OBD counters for companies that lack engineStates.
        return await self._engine_hours_from_obd(start, now)

    # ── engineStates primary path ────────────────────────────────

    async def _engine_hours_from_states(
        self,
        states_raw: dict[str, dict],
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Compute engine hours from CAN bus engineStates events.

        engineStates values: "On" (driving), "Idle", "Off".
        Miles come from a parallel obdOdometerMeters fetch.
        """
        odo_raw = await self._get_paginated_history(
            "obdOdometerMeters", start, end=end,
        )

        fleet = await self.get_fleet_overview()
        active_names = {v["name"].lower() for v in fleet}

        odo_by_id: dict[str, list] = {}
        for vid, v in odo_raw.items():
            odo_by_id[vid] = v.get("obdOdometerMeters", [])

        results: list[dict] = []
        for vid, v in states_raw.items():
            name = v.get("name", "?")
            if name.lower() not in active_names:
                continue

            events = v.get("engineStates", [])
            if len(events) < 2:
                continue

            driving_s = 0.0
            idle_s = 0.0
            for i in range(len(events) - 1):
                try:
                    t1 = datetime.fromisoformat(
                        events[i]["time"].replace("Z", "+00:00"))
                    t2 = datetime.fromisoformat(
                        events[i + 1]["time"].replace("Z", "+00:00"))
                    dur = (t2 - t1).total_seconds()
                    if dur <= 0 or dur > 86400:
                        continue
                    state = events[i]["value"]
                    if state == "On":
                        driving_s += dur
                    elif state == "Idle":
                        idle_s += dur
                except Exception:
                    continue

            engine_s = driving_s + idle_s
            if engine_s <= 0:
                continue

            odo_pts = odo_by_id.get(vid, [])
            miles = 0.0
            if len(odo_pts) >= 2:
                odo_delta_m = odo_pts[-1]["value"] - odo_pts[0]["value"]
                miles = max(0.0, odo_delta_m / METERS_PER_MILE)

            driving_pct = round(driving_s / engine_s * 100)
            idle_pct = 100 - driving_pct

            results.append({
                "id": vid,
                "name": name,
                "_engine_hours": round(engine_s / 3600, 1),
                "_driving_hours": round(driving_s / 3600, 1),
                "_idle_hours": round(idle_s / 3600, 1),
                "_driving_pct": driving_pct,
                "_idle_pct": idle_pct,
                "_miles": round(miles),
                "_engine_s": engine_s,
                "_driving_s": driving_s,
                "_idle_s": idle_s,
            })

        results.sort(key=lambda x: x["name"])
        return results

    # ── OBD fallback path ────────────────────────────────────────

    async def _engine_hours_from_obd(
        self, start: datetime, end: datetime,
    ) -> list[dict]:
        """Compute engine hours from OBD counters (fallback).

        Uses ``obdEngineSeconds`` (cumulative engine counter) and
        ``obdOdometerMeters`` (odometer delta → driving detection).
        """
        raw = await self._get_paginated_history(
            "obdEngineSeconds,obdOdometerMeters", start, end=end,
        )

        fleet = await self.get_fleet_overview()
        active_names = {v["name"].lower() for v in fleet}

        results: list[dict] = []
        for vid, v in raw.items():
            name = v.get("name", "?")
            if name.lower() not in active_names:
                continue

            eng_pts = v.get("obdEngineSeconds", [])
            odo_pts = v.get("obdOdometerMeters", [])

            if len(eng_pts) < 2:
                continue

            eng_delta_s = eng_pts[-1]["value"] - eng_pts[0]["value"]
            if eng_delta_s <= 0:
                continue

            miles = 0.0
            if len(odo_pts) >= 2:
                odo_delta_m = odo_pts[-1]["value"] - odo_pts[0]["value"]
                miles = max(0.0, odo_delta_m / METERS_PER_MILE)

            driving_s = 0.0
            if len(odo_pts) >= 2:
                for i in range(1, len(odo_pts)):
                    try:
                        t1 = datetime.fromisoformat(
                            odo_pts[i - 1]["time"].replace("Z", "+00:00"))
                        t2 = datetime.fromisoformat(
                            odo_pts[i]["time"].replace("Z", "+00:00"))
                        interval = (t2 - t1).total_seconds()
                        if interval <= 0:
                            continue
                        odo_delta = (
                            odo_pts[i]["value"] - odo_pts[i - 1]["value"]
                        )
                        if odo_delta <= 0:
                            continue
                        if interval > 300:
                            avg_speed_kmh = (
                                (odo_delta / 1000) / (interval / 3600)
                            )
                            if avg_speed_kmh < 1.6:
                                continue
                        driving_s += interval
                    except Exception:
                        continue

            driving_s = min(driving_s, eng_delta_s)
            idle_s = eng_delta_s - driving_s

            driving_pct = round(driving_s / eng_delta_s * 100)
            idle_pct = 100 - driving_pct

            results.append({
                "id": vid,
                "name": name,
                "_engine_hours": round(eng_delta_s / 3600, 1),
                "_driving_hours": round(driving_s / 3600, 1),
                "_idle_hours": round(idle_s / 3600, 1),
                "_driving_pct": driving_pct,
                "_idle_pct": idle_pct,
                "_miles": round(miles),
                "_engine_s": eng_delta_s,
                "_driving_s": driving_s,
                "_idle_s": idle_s,
            })

        results.sort(key=lambda x: x["name"])
        return results

    # ── Dashcam Snapshots ─────────────────────────────────────────

    _MAX_VIDEO_BYTES = 25 * 1024 * 1024  # 25 MB download cap
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024   # 5 MB image cap

    async def _get_camera_media(self) -> list[dict]:
        """Fetch camera media (images) via GET /cameras/media.

        Uses a 24-hour window (API max is 1 day).  Returns one result per
        vehicle — the most recent image available.  Only ``image`` media
        type items are used; video items are skipped.

        Each result dict: {vehicle_id, image_url, event_time, trigger}.

        Returns an empty list if the API token lacks "Media Retrieval read"
        permissions or if the endpoint is otherwise unavailable.
        """
        now = datetime.now(timezone.utc)
        end = now.isoformat()
        start = (now - timedelta(hours=23, minutes=59)).isoformat()

        # Build vehicle ID list for batched queries
        try:
            vehicles_data = await self._get("/fleet/vehicles", {"limit": "512"})
            vids = [str(v["id"]) for v in vehicles_data.get("data", [])]
        except Exception as e:
            logger.warning(f"Camera media: failed to fetch vehicles: {e}")
            return []

        if not vids:
            return []

        all_media: list[dict] = []
        for i in range(0, len(vids), 20):
            batch = vids[i:i+20]
            vid_str = ",".join(batch)
            params: dict = {
                "vehicleIds": vid_str,
                "startTime": start,
                "endTime": end,
                "limit": "512",
            }
            try:
                # Paginate within this batch
                for _ in range(50):  # safety limit
                    resp = await self._get("/cameras/media", params)
                    media = resp.get("data", {}).get("media", [])
                    all_media.extend(media)
                    pag = resp.get("pagination", {})
                    if pag.get("hasNextPage") and pag.get("endCursor"):
                        params["after"] = pag["endCursor"]
                    else:
                        break
                params.pop("after", None)
            except SamsaraPermissionError:
                logger.info("Camera media API: token lacks Media Retrieval permission")
                return []
            except Exception as e:
                logger.warning(f"Camera media batch {i//20+1} error: {e}")
                continue

        # Keep only images, pick latest per vehicle
        latest: dict[str, dict] = {}
        for m in all_media:
            if m.get("mediaType") != "image":
                continue
            vid = m.get("vehicleId", "")
            if not vid:
                continue
            ts = m.get("startTime", "")
            if vid not in latest or ts > latest[vid]["startTime"]:
                latest[vid] = m

        logger.info(
            f"Camera media: {len(all_media)} items in 24h, "
            f"{len(latest)} unique vehicles with images"
        )

        results = []
        for vid, m in latest.items():
            url = (m.get("urlInfo") or {}).get("url", "")
            if not url:
                continue
            results.append({
                "vehicle_id": vid,
                "image_url": url,
                "event_time": m.get("startTime", ""),
                "trigger": m.get("triggerReason", ""),
            })
        return results

    async def get_dashcam_snapshots(self, days: int = 10) -> list[dict]:
        """Get one dashcam frame per vehicle from camera media + safety events.

        Two-tier approach:
        1. Primary: GET /cameras/media (24h) — direct JPEG URLs for periodic
           snapshots, trip start/end stills. Covers most active trucks.
           No video download or ffmpeg needed.
        2. Fallback: Safety events (``days`` window) — for any truck that
           wasn't covered by the media API (e.g. token lacks permission,
           or truck only has safety-event video, not periodic stills).

        Returns a list of dicts:
            {vehicle_name, vehicle_id, image_bytes, event_time,
             camera_type, driver_name}
        """
        import subprocess
        import tempfile
        import os

        sem = asyncio.Semaphore(8)
        # Camera media downloads are JPEG blobs (up to ~512 KB) over a
        # CDN URL — slower than a plain JSON call.  15s is the upper
        # bound for "still healthy"; beyond that we abort and the
        # camera-check skips this vehicle this cycle.  The 5s default
        # on ``_get_session`` is too aggressive for image payloads.
        timeout = aiohttp.ClientTimeout(total=15)
        session = self._session or aiohttp.ClientSession(timeout=timeout)

        # ── Tier 1: Camera media API (direct JPEG) ──────────────
        media_items = await self._get_camera_media()

        # Build vehicle name map
        vname_map: dict[str, str] = {}
        try:
            vehicles_data = await self._get("/fleet/vehicles", {"limit": "512"})
            for v in vehicles_data.get("data", []):
                vname_map[str(v["id"])] = v.get("name", "?")
        except Exception as e:
            logger.debug("Could not build vehicle name map: %s", e)

        covered_vehicle_ids: set[str] = set()
        results: list[dict] = []

        async def _download_image(item: dict) -> dict | None:
            """Download a JPEG directly from the media URL."""
            async with sem:
                try:
                    url = item["image_url"]
                    async with session.get(url, timeout=timeout) as resp:
                        if resp.status != 200:
                            return None
                        cl = resp.content_length
                        if cl is not None and cl > self._MAX_IMAGE_BYTES:
                            return None
                        image_bytes = await resp.read()
                        if len(image_bytes) > self._MAX_IMAGE_BYTES:
                            return None
                        if not image_bytes:
                            return None
                        vid = item["vehicle_id"]
                        return {
                            "vehicle_name": vname_map.get(vid, vid),
                            "vehicle_id": vid,
                            "driver_name": "",
                            "event_time": item.get("event_time", ""),
                            "image_bytes": image_bytes,
                            "camera_type": "forward",
                        }
                except Exception as e:
                    logger.debug(f"Media image download failed: {e}")
                return None

        if media_items:
            raw = await asyncio.gather(
                *[_download_image(item) for item in media_items],
                return_exceptions=True,
            )
            for r in raw:
                if isinstance(r, dict):
                    results.append(r)
                    covered_vehicle_ids.add(r["vehicle_id"])

            logger.info(
                f"Camera media: downloaded {len(results)} images "
                f"for {len(covered_vehicle_ids)} vehicles"
            )

        # ── Tier 2: Safety events fallback (video + ffmpeg) ─────
        events = await self.get_events(days=days)
        latest: dict[str, dict] = {}
        for ev in events:
            vid = ev.get("vehicle_id", "")
            if not vid or vid in covered_vehicle_ids:
                continue
            for cam_type, url_key in [
                ("forward", "video_url"),
                ("inward", "inward_video_url"),
            ]:
                url = ev.get(url_key)
                if not url:
                    continue
                cam_key = f"{vid}_{cam_type}"
                if cam_key not in latest:
                    latest[cam_key] = {**ev, "_cam_type": cam_type, "_cam_url": url}

        if latest:
            logger.info(
                f"Camera check fallback: {len(latest)} vehicle/camera combos "
                f"from safety events (not covered by media API)"
            )

            def _extract_frame(video_bytes: bytes) -> bytes | None:
                """Extract middle frame from video bytes (runs in thread)."""
                with tempfile.TemporaryDirectory() as tmp:
                    vid_path = os.path.join(tmp, "clip.mp4")
                    img_path = os.path.join(tmp, "frame.jpg")
                    with open(vid_path, "wb") as f:
                        f.write(video_bytes)

                    dur_proc = subprocess.run(
                        ["ffprobe", "-v", "error",
                         "-show_entries", "format=duration",
                         "-of", "csv=p=0", vid_path],
                        capture_output=True, text=True, timeout=10,
                    )
                    try:
                        duration = float(dur_proc.stdout.strip())
                    except (ValueError, AttributeError):
                        duration = 2.0
                    seek = max(0, duration / 2)

                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(seek),
                         "-i", vid_path, "-frames:v", "1",
                         "-q:v", "2", img_path],
                        capture_output=True, timeout=15,
                    )

                    if os.path.exists(img_path):
                        with open(img_path, "rb") as f:
                            return f.read()
                return None

            async def _process_video(ev: dict) -> dict | None:
                """Download one video and extract frame."""
                async with sem:
                    try:
                        url = ev["_cam_url"]
                        async with session.get(url, timeout=timeout) as resp:
                            if resp.status != 200:
                                return None
                            cl = resp.content_length
                            if cl is not None and cl > self._MAX_VIDEO_BYTES:
                                logger.warning(
                                    f"Dashcam video too large ({cl} bytes) "
                                    f"for {ev.get('vehicle_name', '?')}, skipping"
                                )
                                return None
                            video_bytes = await resp.read()
                            if len(video_bytes) > self._MAX_VIDEO_BYTES:
                                return None

                        image_bytes = await asyncio.to_thread(
                            _extract_frame, video_bytes
                        )
                        if image_bytes:
                            return {
                                "vehicle_name": ev.get("vehicle_name", "?"),
                                "vehicle_id": ev.get("vehicle_id", ""),
                                "driver_name": ev.get("driver_name", ""),
                                "event_time": ev.get("time", ""),
                                "image_bytes": image_bytes,
                                "camera_type": ev.get("_cam_type", "forward"),
                            }
                    except Exception as e:
                        logger.warning(
                            f"Dashcam snapshot failed for "
                            f"{ev.get('vehicle_name', '?')}: {e}"
                        )
                    return None

            fallback_raw = await asyncio.gather(
                *[_process_video(ev) for ev in latest.values()],
                return_exceptions=True,
            )
            for r in fallback_raw:
                if isinstance(r, dict):
                    results.append(r)

        if not self._session:
            await session.close()

        results.sort(key=lambda x: (x["vehicle_name"], x.get("camera_type", "")))
        return results


# ══════════════════════════════════════════════════════════════════
# Multi-Company Client — parallel queries across all companies
# ══════════════════════════════════════════════════════════════════

def build_multi_company_client(
    companies: list,
    base_url: str = "https://api.samsara.com",
    account_id: int | None = None,
) -> "MultiCompanyClient":
    """Build a MultiCompanyClient from database Company objects.

    Args:
        companies: list of database.Company dataclass instances
        base_url: Samsara API base URL
        account_id: owning account (used for Redis key namespacing)
    Returns:
        A ready-to-use MultiCompanyClient
    """
    clients: dict[str, SamsaraClient] = {}
    for co in companies:
        clients[co.code] = SamsaraClient(
            api_key=co.samsara_api_key,
            base_url=base_url,
            active_days=co.active_days,
        )
    return MultiCompanyClient(clients, account_id=account_id)


# Default TTL values (seconds) for cached API responses
_CACHE_TTL_SHORT = 120   # fleet overview, faults, health, fuel
_CACHE_TTL_LONG = 300    # weather, efficiency (historical data)


class MultiCompanyClient:
    """Wraps multiple SamsaraClient instances for parallel multi-company queries.

    Every vehicle dict returned gets an ``_org`` key with the company code.
    Includes a TTL cache so repeated queries within the TTL window are instant.
    """

    def __init__(self, company_clients: dict[str, SamsaraClient], account_id: int | None = None):
        self.clients = company_clients
        self.company_codes = list(company_clients.keys())
        self.account_id = account_id
        self._last_skipped: list[str] = []
        # In-memory TTL cache (fallback when Redis is unavailable)
        self._mem_cache: TTLCache = TTLCache(maxsize=128, ttl=_CACHE_TTL_SHORT)
        self._cache_hits = 0
        self._cache_misses = 0
        # Per-instance org IDs (populated by prefetch/ensure_org_ids)
        self.org_ids: dict[str, str] = {}

    @property
    def last_skipped(self) -> list[str]:
        """Company codes skipped in the most recent _run_per_company call."""
        return self._last_skipped

    @property
    def cache_stats(self) -> dict:
        """Return cache hit/miss counters."""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._mem_cache),
        }

    async def _cache_get(self, key: str):
        """Look up a cached result. Uses Redis if available, else in-memory."""
        # Build namespaced Redis key
        rkey = f"t:{self.account_id}:samsara:{key}" if self.account_id is not None else f"samsara:{key}"
        # Try Redis first
        try:
            from infra.cache import is_available as _redis_ok, get as _redis_get
            if _redis_ok():
                raw = await _redis_get(rkey)
                if raw is not None:
                    self._cache_hits += 1
                    self._last_skipped = raw.get("_skipped", [])
                    return raw.get("data")
        except Exception as e:
            logger.debug("Redis cache read failed, using in-memory: %s", e)

        # Fallback to in-memory
        entry = self._mem_cache.get(key)
        if entry is not None:
            self._cache_hits += 1
            result, skipped = entry
            self._last_skipped = skipped
            return copy.deepcopy(result)

        self._cache_misses += 1
        return None

    async def _cache_set(self, key: str, result, ttl: int = _CACHE_TTL_SHORT):
        """Store a result in the cache (with its associated skipped list)."""
        skipped = list(self._last_skipped)
        # Build namespaced Redis key
        rkey = f"t:{self.account_id}:samsara:{key}" if self.account_id is not None else f"samsara:{key}"

        # Try Redis first
        try:
            from infra.cache import is_available as _redis_ok, cache_set as _redis_set
            if _redis_ok():
                payload = {"data": result, "_skipped": skipped}
                await _redis_set(rkey, payload, ttl=ttl)
                return
        except Exception as e:
            logger.debug("Redis cache write failed, using in-memory: %s", e)

        # Fallback to in-memory
        self._mem_cache[key] = (copy.deepcopy(result), skipped)

    def invalidate_cache(self):
        """Clear all in-memory cached API responses."""
        self._mem_cache.clear()
        logger.info("MultiCompanyClient cache invalidated")

    async def close(self):
        self._mem_cache.clear()
        await asyncio.gather(*(c.close() for c in self.clients.values()),
                             return_exceptions=True)

    async def ensure_org_ids(self):
        """Fetch and cache Samsara org IDs for all companies (idempotent).

        Populates both the instance ``self.org_ids`` dict and the legacy
        module-level ``ORG_IDS`` dict.  Skips companies whose org ID is
        already known.  Errors are logged and silently ignored so alerts
        still fire even if the dashboard link is unavailable.
        """
        tasks = {}
        for code, client in self.clients.items():
            if code not in self.org_ids:
                tasks[code] = client.get_org_id()
        if not tasks:
            return
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for code, result in zip(tasks, results):
            if isinstance(result, str) and result:
                self.org_ids[code] = result
            elif isinstance(result, Exception):
                logger.warning("Could not fetch org ID for %s: %s", code, result)

    # ── helpers ──────────────────────────────────────────────────

    async def prefetch_org_ids(self):
        """Fetch and cache Samsara org IDs for all companies (call once at startup)."""
        tasks = {code: client.get_org_id() for code, client in self.clients.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for code, result in zip(tasks, results):
            if isinstance(result, str) and result:
                self.org_ids[code] = result
                logger.info(f"Samsara org ID for {code}: {result}")
            else:
                logger.warning(f"Failed to get org ID for {code}: {result}")

    def _tag(self, vehicles: list[dict], company: str) -> list[dict]:
        client = self.clients.get(company)
        org_id = client._org_id if client else None
        for v in vehicles:
            v["_org"] = company
            if org_id:
                v["_org_id"] = org_id
        return vehicles

    def vehicle_url(self, company: str, vehicle_id: str) -> str | None:
        """Build a Samsara dashboard URL for a vehicle in a given company."""
        client = self.clients.get(company)
        if client:
            return client.vehicle_url(vehicle_id)
        return None

    async def _run_per_company(self, coro_fn, *args, company: str | None = None):
        """Run *coro_fn(client, *args)* on one or all companies in parallel.
        Returns dict  {company_code: result}.
        """
        if company:
            client = self.clients.get(company)
            if not client:
                raise ValueError(f"Unknown company: {company}")
            return {company: await coro_fn(client, *args)}

        tasks = {code: asyncio.create_task(coro_fn(client, *args))
                 for code, client in self.clients.items()}
        results = {}
        skipped = []
        for code, task in tasks.items():
            try:
                results[code] = await task
            except Exception as e:
                logger.error(f"[{code}] query failed: {e}")
                skipped.append(code)
        if skipped:
            logger.warning(f"Companies skipped due to errors: {', '.join(skipped)}")
            self._last_skipped = skipped
        else:
            self._last_skipped = []
        return results

    # ── fleet overview ───────────────────────────────────────────

    async def get_fleet_overview(self, company: str | None = None) -> list[dict]:
        cache_key = f"fleet_overview:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_fleet_overview()

        per_co = await self._run_per_company(_fn, company=company)
        combined = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        await self._cache_set(cache_key, combined)
        return combined

    # ── drivers ──────────────────────────────────────────────────

    async def get_drivers(self, company: str | None = None) -> list[dict]:
        """Drivers from every (or one) Samsara org, tagged with ``_org``.

        Powers the dashboard's Samsara-driver picker and the daily
        sync job that flags drift between local ``users.samsara_driver_id``
        and the live Samsara fleet.  Cached at the same TTL as the
        vehicle list — driver rosters churn at the same cadence.
        """
        cache_key = f"drivers:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_drivers()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, drivers in per_co.items():
            combined.extend(self._tag(drivers, code))
        # Active first (Samsara doesn't sort), then by name for stable UI.
        combined.sort(key=lambda d: (
            bool(d.get("deactivatedAtMs")),  # active (None/0) first
            (d.get("name") or "").lower(),
        ))
        await self._cache_set(cache_key, combined)
        return combined

    # ── faults ───────────────────────────────────────────────────

    async def get_vehicles_with_faults(
        self, company: str | None = None,
    ) -> tuple[list[dict], int, dict[str, dict]]:
        """Return (faulted_list, total_active, company_breakdown).

        company_breakdown: {code: {"total": int, "faulted": int, "dtcs": int}}
        """
        cache_key = f"faults:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_vehicles_with_faults()

        per_co = await self._run_per_company(_fn, company=company)

        all_faulted: list[dict] = []
        grand_total = 0
        breakdown: dict[str, dict] = {}

        for code, (faulted, total) in per_co.items():
            self._tag(faulted, code)
            dtc_count = sum(len(v.get("_dtcs", [])) for v in faulted)
            all_faulted.extend(faulted)
            grand_total += total
            breakdown[code] = {
                "total": total,
                "faulted": len(faulted),
                "dtcs": dtc_count,
            }

        result = (all_faulted, grand_total, breakdown)
        await self._cache_set(cache_key, result)
        return result

    # ── low fuel ─────────────────────────────────────────────────

    async def get_low_fuel_vehicles(
        self, threshold: int = 20, company: str | None = None,
    ) -> list[dict]:
        cache_key = f"low_fuel:{threshold}:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c, thr):
            return await c.get_low_fuel_vehicles(thr)

        per_co = await self._run_per_company(_fn, threshold, company=company)
        combined = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: x.get("_fuel_pct", 999))
        await self._cache_set(cache_key, combined)
        return combined

    async def get_current_odometer_readings(
        self, company: str | None = None,
    ) -> list[dict]:
        """Return current odometer readings (miles) for all vehicles."""
        cache_key = f"odometer:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_current_odometer_readings()

        per_co = await self._run_per_company(_fn, company=company)
        combined = []
        for code, readings in per_co.items():
            for r in readings:
                r["_org"] = code
            combined.extend(readings)
        combined.sort(key=lambda x: x.get("name", ""))
        await self._cache_set(cache_key, combined)
        return combined

    async def get_current_engine_hours_readings(
        self, company: str | None = None,
    ) -> list[dict]:
        """Return cumulative engine hours per vehicle — parallel to
        ``get_current_odometer_readings`` for multi-company accounts."""
        cache_key = f"engine_hours:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_current_engine_hours_readings()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, readings in per_co.items():
            for r in readings:
                r["_org"] = code
            combined.extend(readings)
        combined.sort(key=lambda x: x.get("name", ""))
        await self._cache_set(cache_key, combined)
        return combined

    # ── single truck lookup ──────────────────────────────────────

    async def get_vehicle_detail(
        self, vehicle_name: str, company: str | None = None,
    ) -> list[dict]:
        """Search all (or one) company for a truck name.
        Returns a list — may contain 0, 1, or 2+ matches.
        """
        async def _fn(c):
            return await c.get_vehicle_detail(vehicle_name)

        per_co = await self._run_per_company(_fn, company=company)
        matches = []
        for code, vehicle in per_co.items():
            if vehicle:
                vehicle["_org"] = code
                client = self.clients.get(code)
                if client and client._org_id:
                    vehicle["_org_id"] = client._org_id
                matches.append(vehicle)
        return matches

    # ── engine hours ─────────────────────────────────────────────

    async def get_engine_hours(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get engine hours + driving/idle breakdown across companies."""
        async def _fn(c):
            return await c.get_engine_hours(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        return combined

    # ── vehicle health ───────────────────────────────────────────

    async def get_vehicle_health(
        self, company: str | None = None,
    ) -> list[dict]:
        """Get vehicle health diagnostics across companies."""
        cache_key = f"health:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_vehicle_health()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (-len(x.get("_health_alerts", [])),
                                      x.get("_org", ""), x["name"]))
        await self._cache_set(cache_key, combined)
        return combined

    # ── fleet weather ────────────────────────────────────────────

    async def get_fleet_weather(
        self, company: str | None = None,
    ) -> list[dict]:
        """Get ambient weather conditions across companies."""
        cache_key = f"weather:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_fleet_weather()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        # Sort by temperature ascending (coldest first)
        combined.sort(key=lambda x: x.get("_weather", {}).get("temp_f", 999))
        await self._cache_set(cache_key, combined)
        return combined

    # ── driver efficiency ────────────────────────────────────────

    async def get_driver_efficiency(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get driver efficiency across companies (cached)."""
        cache_key = f"driver_efficiency:{days}:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_driver_efficiency(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, drivers in per_co.items():
            for d in drivers:
                d["_org"] = code
            combined.extend(drivers)
        combined.sort(key=lambda x: (x.get("_org", ""), x["driver_name"]))
        await self._cache_set(cache_key, combined)
        return combined

    # ── fleet efficiency (merged) ────────────────────────────────

    async def get_fleet_efficiency(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get merged engine hours + driver efficiency across companies."""
        cache_key = f"efficiency:{days}:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_fleet_efficiency(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        await self._cache_set(cache_key, combined)
        return combined

    # ── geofences ────────────────────────────────────────────────

    async def get_geofences(
        self, company: str | None = None,
    ) -> list[dict]:
        """Get geofences across companies."""
        cache_key = f"geofences:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_geofences()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, fences in per_co.items():
            for f in fences:
                f["_org"] = code
            combined.extend(fences)
        await self._cache_set(cache_key, combined, ttl=_CACHE_TTL_LONG)
        return combined

    # ── events ───────────────────────────────────────────────────

    async def get_events(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get safety events across companies."""
        cache_key = f"events:{days}:{company or 'all'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _fn(c):
            return await c.get_events(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, events in per_co.items():
            for e in events:
                e["_org"] = code
            combined.extend(events)
        combined.sort(key=lambda x: x.get("time", ""), reverse=True)
        await self._cache_set(cache_key, combined)
        return combined

    async def get_safety_event(self, event_id: str) -> dict | None:
        """Find a single safety event across all companies.

        Used by /api/safety/events/{id}/video to re-sign Samsara's S3
        URLs on every click (the stored ones expire after 8 hours).
        We don't know upfront which company holds the event, so we
        ask each per-company client and return the first non-empty
        result.  In production this is 1-2 companies per account.

        Returns ``None`` when no company has the event.
        """
        for code, client in self.clients.items():
            try:
                evt = await client.get_safety_event(event_id)
            except Exception as e:
                logger.debug("get_safety_event(%s) failed for %s: %s", event_id, code, e)
                continue
            if evt:
                evt["_org"] = code
                return evt
        return None

    # ── engine states ────────────────────────────────────────────

    async def get_engine_states(
        self, company: str | None = None,
    ) -> list[dict]:
        """Get latest engine states across companies."""
        async def _fn(c):
            return await c.get_engine_states()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, states in per_co.items():
            for s in states:
                s["_org"] = code
            combined.extend(states)
        return combined

    # ── single vehicle location ──────────────────────────────────

    async def get_vehicle_location(
        self, vehicle_id: str, company: str | None = None,
    ) -> dict | None:
        """Get fresh GPS location for a single vehicle across companies."""
        if company:
            client = self.clients.get(company)
            if client:
                return await client.get_vehicle_location(vehicle_id)
            return None
        # Try all companies in parallel
        tasks = {
            code: asyncio.create_task(c.get_vehicle_location(vehicle_id))
            for code, c in self.clients.items()
        }
        for code, task in tasks.items():
            try:
                result = await task
                if result:
                    return result
            except Exception as e:
                logger.debug("get_vehicle_location failed for company %s: %s", code, e)
        return None
