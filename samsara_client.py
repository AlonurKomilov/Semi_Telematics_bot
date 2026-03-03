"""
Samsara API Client — async wrapper for fleet telematics data.

Supports multi-org: each SamsaraClient wraps one org's API key.
MultiOrgClient orchestrates parallel queries across all orgs.

v3 — Database-driven: org display names and API keys come from the
     database layer, not from hardcoded dicts or env vars.
     Legacy `parse_orgs()` and `ORG_DISPLAY` kept as thin compat shims
     so pdf_report.py and formatters.py can still import them during
     transition; callers should migrate to `build_multi_org_client()`.
"""

import asyncio
import re
import aiohttp
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Legacy compat shim — populated at runtime by bot.py ──────────
# pdf_report.py and formatters.py still read ORG_DISPLAY.
# bot.py calls `populate_org_display(orgs)` once at startup.
ORG_DISPLAY: dict[str, str] = {}


def populate_org_display(orgs: list) -> None:
    """Populate ORG_DISPLAY from a list of database Organization objects.

    Called once at bot startup so formatters / pdf_report see the names.
    """
    ORG_DISPLAY.clear()
    for org in orgs:
        ORG_DISPLAY[org.code] = org.display_name or org.code


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

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"Samsara API error on {endpoint}: {e}")
            raise

    # ── Vehicle List ─────────────────────────────────────────────

    async def get_vehicles(self) -> list[dict]:
        """Return list of all vehicles in the fleet."""
        data = await self._get("/fleet/vehicles")
        return data.get("data", [])

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

    # ── Fuel Levels ──────────────────────────────────────────────

    async def get_fuel_levels(self) -> list[dict]:
        """Return fuel percent for all vehicles."""
        data = await self._get("/fleet/vehicles/stats", params={"types": "fuelPercents"})
        return data.get("data", [])

    # ── Drivers ──────────────────────────────────────────────────

    async def get_drivers(self) -> list[dict]:
        """Return list of all fleet drivers."""
        data = await self._get("/fleet/drivers")
        return data.get("data", [])

    # ── Active-vehicle filter ────────────────────────────────────

    def _is_active(self, name: str, location: dict) -> bool:
        """Check if a vehicle should be included.

        Filters out:
          1. Ghost names (Deactivated…, GPUJ-…)
          2. Vehicles with no GPS data
          3. Vehicles whose last GPS ping is older than active_days
             (skip this check when active_days == 0)
        """
        if _SKIP_NAME_RE.search(name):
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
        fuel_by_id = {v["id"]: v.get("fuelPercent", {}) for v in fuel_raw}

        enriched = []
        skipped = 0
        for vid, v in vehicles.items():
            name = v.get("name", "?")
            loc  = loc_by_id.get(vid, {})

            if not self._is_active(name, loc):
                skipped += 1
                continue

            enriched.append({
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
            })

        if skipped:
            logger.info(f"Filtered out {skipped} inactive vehicles "
                        f"(active_days={self.active_days})")

        # Sort by name (truck number)
        enriched.sort(key=lambda x: x["name"])
        return enriched

    async def get_vehicle_detail(self, truck_name: str) -> Optional[dict]:
        """
        Get enriched data for a single vehicle by truck name/number.
        """
        fleet = await self.get_fleet_overview()
        truck_name_lower = truck_name.strip().lower()
        for v in fleet:
            if v["name"].lower() == truck_name_lower:
                return v
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

    async def get_critical_faults(self) -> list[dict]:
        """
        Return vehicles with critical faults:
        - STOP light on
        - PROTECT light on
        - EMISSIONS light on
        - Any FMI with 'most severe' in description
        """
        faulted, total = await self.get_vehicles_with_faults()
        critical = []
        for v in faulted:
            lights = v.get("_lights", {})
            is_critical = (
                lights.get("stopIsOn", False)
                or lights.get("protectIsOn", False)
                or lights.get("emissionsIsOn", False)
            )
            # Also check for severe FMI descriptions
            if not is_critical:
                for dtc in v.get("_dtcs", []):
                    fmi_desc = dtc.get("fmiDescription", "").lower()
                    if "most severe" in fmi_desc:
                        is_critical = True
                        break
            if is_critical:
                critical.append(v)
        return critical

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


# ══════════════════════════════════════════════════════════════════
# Multi-Org Client — parallel queries across all organizations
# ══════════════════════════════════════════════════════════════════

def parse_orgs(env_value: str, base_url: str = "https://api.samsara.com",
               active_days: int = 30) -> dict[str, "SamsaraClient"]:
    """Parse SAMSARA_ORGS env var → dict of {code: SamsaraClient}.

    LEGACY — kept for backwards compatibility & seed migration.
    New code should use build_multi_org_client().

    Format: CODE:api_key,CODE:api_key,...
    Example: PTG:samsara_api_xxx,CFT:samsara_api_yyy
    """
    clients: dict[str, SamsaraClient] = {}
    for entry in env_value.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        code, key = entry.split(":", 1)
        code = code.strip().upper()
        key = key.strip()
        if code and key:
            clients[code] = SamsaraClient(key, base_url, active_days=active_days)
    return clients


def build_multi_org_client(
    orgs: list,
    base_url: str = "https://api.samsara.com",
) -> "MultiOrgClient":
    """Build a MultiOrgClient from database Organization objects.

    Args:
        orgs: list of database.Organization dataclass instances
        base_url: Samsara API base URL
    Returns:
        A ready-to-use MultiOrgClient
    """
    clients: dict[str, SamsaraClient] = {}
    for org in orgs:
        clients[org.code] = SamsaraClient(
            api_key=org.samsara_api_key,
            base_url=base_url,
            active_days=org.active_days,
        )
    return MultiOrgClient(clients)


class MultiOrgClient:
    """Wraps multiple SamsaraClient instances for parallel multi-org queries.

    Every vehicle dict returned gets an ``_org`` key with the org code.
    """

    def __init__(self, org_clients: dict[str, SamsaraClient]):
        self.clients = org_clients
        self.org_codes = list(org_clients.keys())

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients.values()),
                             return_exceptions=True)

    # ── helpers ──────────────────────────────────────────────────

    def _tag(self, vehicles: list[dict], org: str) -> list[dict]:
        for v in vehicles:
            v["_org"] = org
        return vehicles

    async def _run_per_org(self, coro_fn, *args, org: str | None = None):
        """Run *coro_fn(client, *args)* on one or all orgs in parallel.
        Returns dict  {org_code: result}.
        """
        if org:
            client = self.clients.get(org)
            if not client:
                raise ValueError(f"Unknown org: {org}")
            return {org: await coro_fn(client, *args)}

        tasks = {code: asyncio.create_task(coro_fn(client, *args))
                 for code, client in self.clients.items()}
        results = {}
        for code, task in tasks.items():
            try:
                results[code] = await task
            except Exception as e:
                logger.error(f"[{code}] query failed: {e}")
        return results

    # ── fleet overview ───────────────────────────────────────────

    async def get_fleet_overview(self, org: str | None = None) -> list[dict]:
        async def _fn(c):
            return await c.get_fleet_overview()

        per_org = await self._run_per_org(_fn, org=org)
        combined = []
        for code, vehicles in per_org.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        return combined

    # ── faults ───────────────────────────────────────────────────

    async def get_vehicles_with_faults(
        self, org: str | None = None,
    ) -> tuple[list[dict], int, dict[str, dict]]:
        """Return (faulted_list, total_active, org_breakdown).

        org_breakdown: {code: {"total": int, "faulted": int, "dtcs": int}}
        """
        async def _fn(c):
            return await c.get_vehicles_with_faults()

        per_org = await self._run_per_org(_fn, org=org)

        all_faulted: list[dict] = []
        grand_total = 0
        breakdown: dict[str, dict] = {}

        for code, (faulted, total) in per_org.items():
            self._tag(faulted, code)
            dtc_count = sum(len(v.get("_dtcs", [])) for v in faulted)
            all_faulted.extend(faulted)
            grand_total += total
            breakdown[code] = {
                "total": total,
                "faulted": len(faulted),
                "dtcs": dtc_count,
            }

        return all_faulted, grand_total, breakdown

    # ── critical faults ──────────────────────────────────────────

    async def get_critical_faults(self, org: str | None = None) -> list[dict]:
        async def _fn(c):
            return await c.get_critical_faults()

        per_org = await self._run_per_org(_fn, org=org)
        combined = []
        for code, vehicles in per_org.items():
            combined.extend(self._tag(vehicles, code))
        return combined

    # ── low fuel ─────────────────────────────────────────────────

    async def get_low_fuel_vehicles(
        self, threshold: int = 20, org: str | None = None,
    ) -> list[dict]:
        async def _fn(c, thr):
            return await c.get_low_fuel_vehicles(thr)

        per_org = await self._run_per_org(_fn, threshold, org=org)
        combined = []
        for code, vehicles in per_org.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: x.get("_fuel_pct", 999))
        return combined

    # ── single truck lookup ──────────────────────────────────────

    async def get_vehicle_detail(
        self, truck_name: str, org: str | None = None,
    ) -> list[dict]:
        """Search all (or one) org for a truck name.
        Returns a list — may contain 0, 1, or 2+ matches.
        """
        async def _fn(c):
            return await c.get_vehicle_detail(truck_name)

        per_org = await self._run_per_org(_fn, org=org)
        matches = []
        for code, vehicle in per_org.items():
            if vehicle:
                vehicle["_org"] = code
                matches.append(vehicle)
        return matches
