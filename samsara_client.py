"""
Samsara API Client — async wrapper for fleet telematics data.
"""

import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SamsaraClient:
    """Async client for the Samsara REST API."""

    def __init__(self, api_key: str, base_url: str = "https://api.samsara.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
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

    # ── Combined: Enriched Vehicle Data ──────────────────────────

    async def get_fleet_overview(self) -> list[dict]:
        """
        Build an enriched list of vehicles by merging:
        vehicle info + fault codes + GPS + fuel.
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
        for vid, v in vehicles.items():
            enriched.append({
                "id": vid,
                "name": v.get("name", "?"),
                "vin": v.get("vin", "N/A"),
                "make": v.get("make", "N/A"),
                "model": v.get("model", "N/A"),
                "year": v.get("year", "N/A"),
                "license_plate": v.get("licensePlate", "N/A"),
                "fault_codes": faults_by_id.get(vid, {}),
                "location": loc_by_id.get(vid, {}),
                "fuel": fuel_by_id.get(vid, {}),
            })

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

    async def get_vehicles_with_faults(self) -> list[dict]:
        """Return only vehicles that have active diagnostic trouble codes."""
        fleet = await self.get_fleet_overview()
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
        return result

    async def get_critical_faults(self) -> list[dict]:
        """
        Return vehicles with critical faults:
        - STOP light on
        - PROTECT light on
        - EMISSIONS light on
        - Any FMI with 'most severe' in description
        """
        faulted = await self.get_vehicles_with_faults()
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
