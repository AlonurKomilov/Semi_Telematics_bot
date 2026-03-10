"""
Samsara API Client — async wrapper for fleet telematics data.

Supports multi-company: each SamsaraClient wraps one company's API key.
MultiCompanyClient orchestrates parallel queries across all companies.

v3 — Database-driven: company display names and API keys come from the
     database layer, not from hardcoded dicts or env vars.
     Use `build_multi_company_client()` to create clients from DB Company objects.
"""

import asyncio
import re
import aiohttp
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class SamsaraPermissionError(Exception):
    """Raised when the API token lacks required permissions."""
    pass


# ── Legacy compat shim — populated at runtime by bot.py ──────────
# pdf_generator.py and formatters.py still read COMPANY_DISPLAY.
# bot.py calls `populate_company_display(companies)` once at startup for display names.
COMPANY_DISPLAY: dict[str, str] = {}


def populate_company_display(companies: list) -> None:
    """Populate COMPANY_DISPLAY from a list of database Company objects.

    Called once at bot startup so formatters / pdf_generator see the names.
    """
    COMPANY_DISPLAY.clear()
    for co in companies:
        COMPANY_DISPLAY[co.code] = co.display_name or co.code


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
                if resp.status in (401, 403):
                    try:
                        body = await resp.json()
                        msg = body.get("message", "Permission denied")
                    except Exception:
                        msg = f"HTTP {resp.status} — permission denied"
                    raise SamsaraPermissionError(msg)
                resp.raise_for_status()
                return await resp.json()
        except SamsaraPermissionError:
            raise
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
        """Return fuel percent and DEF level for all vehicles.

        Non-fatal: if the Samsara endpoint returns an error (e.g. 400
        for fleets that don't support defLevelMilliPercent), return an
        empty list so the rest of the data pipeline keeps working.
        """
        try:
            data = await self._get("/fleet/vehicles/stats",
                                   params={"types": "fuelPercents,defLevelMilliPercent"})
            return data.get("data", [])
        except Exception as e:
            logger.warning(f"Fuel stats unavailable (non-fatal): {e}")
            return []

    # ── Drivers ──────────────────────────────────────────────────

    async def get_drivers(self) -> list[dict]:
        """Return list of all fleet drivers."""
        data = await self._get("/fleet/drivers")
        return data.get("data", [])

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
        fuel_by_id = {v["id"]: v.get("fuelPercent", {}) for v in fuel_raw}
        def_by_id: dict[str, dict] = {}
        for fv in fuel_raw:
            d = fv.get("defLevelMilliPercent", {})
            if d.get("value") is not None:
                def_by_id[fv["id"]] = {
                    "value": round(d["value"] / 1000, 1),
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
                "def_level": def_by_id.get(vid, {}),
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

        Unlike get_fleet_overview(), this searches ALL vehicles (including
        those without a gateway) so direct truck lookups always work.
        """
        vehicles_raw = await self.get_vehicles()
        fault_raw = await self.get_fault_codes()
        location_raw = await self.get_locations()
        fuel_raw = await self.get_fuel_levels()

        faults_by_id = {v["id"]: v.get("faultCodes", {}) for v in fault_raw}
        loc_by_id = {v["id"]: v.get("location", {}) for v in location_raw}
        fuel_by_id = {v["id"]: v.get("fuelPercent", {}) for v in fuel_raw}
        def_by_id: dict[str, dict] = {}
        for fv in fuel_raw:
            d = fv.get("defLevelMilliPercent", {})
            if d.get("value") is not None:
                def_by_id[fv["id"]] = {
                    "value": round(d["value"] / 1000, 1),
                    "time": d.get("time", ""),
                }

        truck_name_lower = truck_name.strip().lower()
        for v in vehicles_raw:
            name = v.get("name", "?")
            if name.lower() != truck_name_lower:
                continue
            if _SKIP_NAME_RE.search(name):
                continue

            vid = v["id"]
            loc = loc_by_id.get(vid, {})
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

        # batch1 includes defLevelMilliPercent which some fleets don't
        # support (400 Bad Request). Make it non-fatal.
        try:
            data1, data2 = await asyncio.gather(
                self._get("/fleet/vehicles/stats", params={"types": batch1}),
                self._get("/fleet/vehicles/stats", params={"types": batch2}),
            )
        except Exception:
            # Retry batch1 without DEF, fetch batch2 normally
            logger.warning("Health batch1 failed, retrying without defLevelMilliPercent")
            batch1_no_def = "engineCoolantTemperatureMilliC,batteryMilliVolts,engineOilPressureKPa"
            data1, data2 = await asyncio.gather(
                self._get("/fleet/vehicles/stats", params={"types": batch1_no_def}),
                self._get("/fleet/vehicles/stats", params={"types": batch2}),
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

            # Engine state (derived from RPM)
            health["engine_on"] = bool(rpm_val and rpm_val > 0)

            # Count alerts
            alerts = []
            if health.get("battery_v") is not None and health["battery_v"] < 12.2:
                alerts.append("low_battery")
            if health.get("oil_psi") is not None and health["oil_psi"] < 10:
                alerts.append("low_oil_pressure")
            if health.get("coolant_c") is not None and health["coolant_c"] > 105:
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
            driver = s.get("driver", {})
            dist_m = s.get("totalDistanceDrivenMeters", 0)
            drive_ms = s.get("totalDriveTimeDurationMs", 0)
            idle_ms = s.get("totalIdleTimeDurationMs", 0)
            fuel_ml = s.get("totalFuelConsumedMl", 0)
            green_ms = s.get("greenBandDrivingDurationMs", 0)
            coast_ms = s.get("coastingDurationMs", 0)
            cruise_ms = s.get("cruiseControlDurationMs", 0)
            antic_brk = s.get("anticipationBrakeEventCount", 0)
            total_brk = s.get("totalBrakeEventCount", 0)
            hi_torque_ms = s.get("highTorqueMs", 0)
            overspeed_ms = s.get("overSpeedMs", 0)

            total_ms = drive_ms + idle_ms
            if total_ms <= 0:
                continue

            miles = dist_m / 1609.34
            fuel_gal = fuel_ml / 3785.41
            mpg = miles / fuel_gal if fuel_gal > 0 else 0
            drive_pct = round(drive_ms / total_ms * 100)
            idle_pct = 100 - drive_pct
            green_pct = round(green_ms / drive_ms * 100) if drive_ms > 0 else 0

            results.append({
                "driver_id": driver.get("id", ""),
                "driver_name": driver.get("name", "?"),
                "_miles": round(miles, 1),
                "_drive_h": round(drive_ms / 3600000, 1),
                "_idle_h": round(idle_ms / 3600000, 1),
                "_drive_pct": drive_pct,
                "_idle_pct": idle_pct,
                "_fuel_gal": round(fuel_gal, 1),
                "_mpg": round(mpg, 1),
                "_green_pct": green_pct,
                "_coast_min": round(coast_ms / 60000, 1),
                "_cruise_min": round(cruise_ms / 60000, 1),
                "_overspeed_min": round(overspeed_ms / 60000, 1),
                "_hi_torque_min": round(hi_torque_ms / 60000, 1),
                "_antic_brakes": antic_brk,
                "_total_brakes": total_brk,
                "_antic_pct": round(antic_brk / total_brk * 100) if total_brk > 0 else 0,
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
        eng_task = asyncio.create_task(self.get_engine_hours(days))
        try:
            drv_task = asyncio.create_task(self.get_driver_efficiency(days))
            vehicles, drivers = await asyncio.gather(eng_task, drv_task)
        except Exception:
            # Efficiency API may fail (license issue) — engine hours alone
            vehicles = await eng_task
            drivers = []

        # Build lookup: truck_name (lowercase) → driver efficiency per-vehicle
        driver_by_truck: dict[str, dict] = {}
        for drv in drivers:
            for vs in drv.get("_vehicle_summaries", []):
                veh = vs.get("vehicle", {})
                vname = (veh.get("name") or "").strip().lower()
                if not vname:
                    continue
                fuel_ml = vs.get("fuelConsumedMl", 0)
                dist_m = vs.get("distanceDrivenMeters", 0)
                drive_ms = vs.get("driveTimeDurationMs", 0)
                fuel_gal = fuel_ml / 3785.41
                miles = dist_m / 1609.34
                mpg = miles / fuel_gal if fuel_gal > 0 else 0
                green_ms = vs.get("greenBandDrivingDurationMs", 0)
                green_pct = round(green_ms / drive_ms * 100) if drive_ms > 0 else 0
                overspeed_ms = vs.get("overSpeedMs", 0)
                antic_brk = vs.get("anticipationBrakeEventCount", 0)
                total_brk = vs.get("totalBrakeEventCount", 0)

                driver_by_truck[vname] = {
                    "_driver_name": drv["driver_name"],
                    "_fuel_gal": round(fuel_gal, 1),
                    "_mpg": round(mpg, 1),
                    "_green_pct": green_pct,
                    "_overspeed_min": round(overspeed_ms / 60000, 1),
                    "_antic_brakes": antic_brk,
                    "_total_brakes": total_brk,
                    "_antic_pct": round(antic_brk / total_brk * 100) if total_brk > 0 else 0,
                }

        # Merge driver data into truck records
        for v in vehicles:
            truck_key = v["name"].strip().lower()
            drv_data = driver_by_truck.get(truck_key)
            if drv_data:
                v.update(drv_data)
            else:
                v["_driver_name"] = None
                v["_fuel_gal"] = None
                v["_mpg"] = None
                v["_green_pct"] = None
                v["_overspeed_min"] = None
                v["_antic_brakes"] = None
                v["_total_brakes"] = None
                v["_antic_pct"] = None

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
                miles = max(0.0, odo_delta_m / 1609.34)

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
                miles = max(0.0, odo_delta_m / 1609.34)

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


# ══════════════════════════════════════════════════════════════════
# Multi-Company Client — parallel queries across all companies
# ══════════════════════════════════════════════════════════════════

def build_multi_company_client(
    companies: list,
    base_url: str = "https://api.samsara.com",
) -> "MultiCompanyClient":
    """Build a MultiCompanyClient from database Company objects.

    Args:
        companies: list of database.Company dataclass instances
        base_url: Samsara API base URL
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
    return MultiCompanyClient(clients)


class MultiCompanyClient:
    """Wraps multiple SamsaraClient instances for parallel multi-company queries.

    Every vehicle dict returned gets an ``_org`` key with the company code.
    """

    def __init__(self, company_clients: dict[str, SamsaraClient]):
        self.clients = company_clients
        self.company_codes = list(company_clients.keys())
        self._last_skipped: list[str] = []

    @property
    def last_skipped(self) -> list[str]:
        """Company codes skipped in the most recent _run_per_company call."""
        return self._last_skipped

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients.values()),
                             return_exceptions=True)

    # ── helpers ──────────────────────────────────────────────────

    def _tag(self, vehicles: list[dict], company: str) -> list[dict]:
        for v in vehicles:
            v["_org"] = company
        return vehicles

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
        async def _fn(c):
            return await c.get_fleet_overview()

        per_co = await self._run_per_company(_fn, company=company)
        combined = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        return combined

    # ── faults ───────────────────────────────────────────────────

    async def get_vehicles_with_faults(
        self, company: str | None = None,
    ) -> tuple[list[dict], int, dict[str, dict]]:
        """Return (faulted_list, total_active, company_breakdown).

        company_breakdown: {code: {"total": int, "faulted": int, "dtcs": int}}
        """
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

        return all_faulted, grand_total, breakdown

    # ── critical faults ──────────────────────────────────────────

    async def get_critical_faults(self, company: str | None = None) -> list[dict]:
        async def _fn(c):
            return await c.get_critical_faults()

        per_co = await self._run_per_company(_fn, company=company)
        combined = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        return combined

    # ── low fuel ─────────────────────────────────────────────────

    async def get_low_fuel_vehicles(
        self, threshold: int = 20, company: str | None = None,
    ) -> list[dict]:
        async def _fn(c, thr):
            return await c.get_low_fuel_vehicles(thr)

        per_co = await self._run_per_company(_fn, threshold, company=company)
        combined = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: x.get("_fuel_pct", 999))
        return combined

    # ── single truck lookup ──────────────────────────────────────

    async def get_vehicle_detail(
        self, truck_name: str, company: str | None = None,
    ) -> list[dict]:
        """Search all (or one) company for a truck name.
        Returns a list — may contain 0, 1, or 2+ matches.
        """
        async def _fn(c):
            return await c.get_vehicle_detail(truck_name)

        per_co = await self._run_per_company(_fn, company=company)
        matches = []
        for code, vehicle in per_co.items():
            if vehicle:
                vehicle["_org"] = code
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
        async def _fn(c):
            return await c.get_vehicle_health()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (-len(x.get("_health_alerts", [])),
                                      x.get("_org", ""), x["name"]))
        return combined

    # ── fleet weather ────────────────────────────────────────────

    async def get_fleet_weather(
        self, company: str | None = None,
    ) -> list[dict]:
        """Get ambient weather conditions across companies."""
        async def _fn(c):
            return await c.get_fleet_weather()

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        # Sort by temperature ascending (coldest first)
        combined.sort(key=lambda x: x.get("_weather", {}).get("temp_f", 999))
        return combined

    # ── driver efficiency ────────────────────────────────────────

    async def get_driver_efficiency(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get driver efficiency across companies."""
        async def _fn(c):
            return await c.get_driver_efficiency(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, drivers in per_co.items():
            for d in drivers:
                d["_org"] = code
            combined.extend(drivers)
        combined.sort(key=lambda x: (x.get("_org", ""), x["driver_name"]))
        return combined

    # ── fleet efficiency (merged) ────────────────────────────────

    async def get_fleet_efficiency(
        self, days: int = 7, company: str | None = None,
    ) -> list[dict]:
        """Get merged engine hours + driver efficiency across companies."""
        async def _fn(c):
            return await c.get_fleet_efficiency(days)

        per_co = await self._run_per_company(_fn, company=company)
        combined: list[dict] = []
        for code, vehicles in per_co.items():
            combined.extend(self._tag(vehicles, code))
        combined.sort(key=lambda x: (x.get("_org", ""), x["name"]))
        return combined
