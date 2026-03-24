"""CSV export generators for Semi-Telematics bot reports."""

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ_ET = ZoneInfo("America/New_York")
_BOM = "\ufeff"

_NA = "N/A"


def _val(v, default=_NA):
    """Return v if not None, else default."""
    return v if v is not None else default


def _now_et() -> str:
    return datetime.now(_TZ_ET).strftime("%B %d, %Y  %I:%M %p %Z")


def _fmt_iso_et(iso_str: str | None) -> str:
    """Convert ISO-8601 string to readable ET timestamp."""
    if not iso_str:
        return _NA
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(_TZ_ET).strftime("%m/%d/%Y %I:%M %p")
    except (ValueError, TypeError):
        return _NA


def _to_buf(sio: io.StringIO) -> io.BytesIO:
    """Encode StringIO to BytesIO with UTF-8 BOM."""
    buf = io.BytesIO((_BOM + sio.getvalue()).encode("utf-8"))
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# EFFICIENCY CSV
# ══════════════════════════════════════════════════════════════════

def generate_efficiency_csv(
    vehicles: list[dict],
    days: int = 7,
    company_filter: str | None = None,
) -> io.BytesIO:
    """Return a BytesIO containing CSV data for the Efficiency report."""
    sio = io.StringIO()
    writer = csv.writer(sio)

    # Metadata rows
    writer.writerow(["Report", "Fleet Efficiency"])
    writer.writerow(["Generated", _now_et()])
    writer.writerow(["Period", f"Past {days} Days"])
    writer.writerow(["Company", company_filter or "All Companies"])
    writer.writerow([])  # blank separator

    header = [
        "Truck",
        "Company",
        "Driver",
        "Engine (hours)",
        "Miles",
        "Drive (hours)",
        "Idle (hours)",
        "Drive %",
        "Idle %",
        "Fuel (gallons)",
        "MPG",
        "Eco %",
        "Overspeed (min)",
        "Antic Brakes",
        "Total Brakes",
        "Antic %",
    ]
    writer.writerow(header)

    # Sort by company then truck
    sorted_vehicles = sorted(vehicles, key=lambda x: (x.get("_org", ""), x.get("name", "")))

    for v in sorted_vehicles:
        writer.writerow([
            v.get("name", _NA),
            v.get("_org", _NA),
            v.get("_driver_name") or _NA,
            _val(v.get("_engine_hours")),
            _val(v.get("_miles")),
            _val(v.get("_driving_hours")),
            _val(v.get("_idle_hours")),
            _val(v.get("_driving_pct")),
            _val(v.get("_idle_pct")),
            _val(v.get("_fuel_gal")),
            _val(v.get("_mpg")),
            _val(v.get("_green_pct")),
            _val(v.get("_overspeed_min")),
            _val(v.get("_antic_brakes")),
            _val(v.get("_total_brakes")),
            _val(v.get("_antic_pct")),
        ])

    # Totals row
    total_eng = sum(v.get("_engine_hours", 0) or 0 for v in vehicles)
    total_miles = sum(v.get("_miles", 0) or 0 for v in vehicles)
    total_drv = sum(v.get("_driving_hours", 0) or 0 for v in vehicles)
    total_idle = sum(v.get("_idle_hours", 0) or 0 for v in vehicles)
    avg_drv_pct = round(total_drv / total_eng * 100) if total_eng > 0 else 0
    avg_idle_pct = 100 - avg_drv_pct if total_eng > 0 else 0
    with_fuel = [v for v in vehicles if v.get("_fuel_gal")]
    total_fuel = sum(v["_fuel_gal"] for v in with_fuel)
    fleet_mpg = round(sum(v.get("_miles", 0) for v in with_fuel) / total_fuel, 1) if total_fuel > 0 else _NA
    writer.writerow([])
    writer.writerow([
        "TOTALS", "", f"{len(vehicles)} trucks",
        round(total_eng, 1), round(total_miles),
        round(total_drv, 1), round(total_idle, 1),
        f"{avg_drv_pct}%", f"{avg_idle_pct}%",
        round(total_fuel, 1) if total_fuel else _NA,
        fleet_mpg, "", "", "", "", "",
    ])

    return _to_buf(sio)


# ══════════════════════════════════════════════════════════════════
# FUEL & DEF CSV
# ══════════════════════════════════════════════════════════════════

def generate_fuel_csv(
    all_vehicles: list[dict],
    company_filter: str | None = None,
) -> io.BytesIO:
    """Return a BytesIO containing CSV data for the Fuel & DEF report."""
    sio = io.StringIO()
    writer = csv.writer(sio)

    # Metadata
    writer.writerow(["Report", "Fleet Fuel & DEF"])
    writer.writerow(["Generated", _now_et()])
    writer.writerow(["Company", company_filter or "All Companies"])
    writer.writerow([])

    header = [
        "Truck",
        "Company",
        "Fuel (%)",
        "DEF (%)",
        "Status",
        "Fuel Updated",
        "Location",
        "VIN",
        "Year",
        "Make",
        "Model",
    ]
    writer.writerow(header)

    def _fuel_status(pct):
        if pct is None:
            return "No Data"
        if pct <= 15:
            return "Critical"
        if pct <= 30:
            return "Low"
        return "Good"

    # Sort by fuel ascending (critical first), None at end
    sorted_v = sorted(
        all_vehicles,
        key=lambda x: (
            x.get("fuel", {}).get("value") is None,
            x.get("fuel", {}).get("value") or 999,
        ),
    )

    for v in sorted_v:
        fuel_pct = v.get("fuel", {}).get("value")
        def_pct = v.get("def_level", {}).get("value")
        loc = v.get("location", {})
        addr = loc.get("formattedAddress") or loc.get("address", "")
        if not addr and loc.get("latitude"):
            addr = f"{loc['latitude']:.4f}, {loc['longitude']:.4f}"

        writer.writerow([
            v.get("name", _NA),
            v.get("_org", _NA),
            _val(fuel_pct),
            _val(def_pct),
            _fuel_status(fuel_pct),
            _fmt_iso_et(v.get("fuel", {}).get("time")),
            addr or _NA,
            v.get("vin", _NA),
            v.get("year", _NA),
            v.get("make", _NA),
            v.get("model", _NA),
        ])

    # Summary row
    known_fuel = [v for v in all_vehicles if v.get("fuel", {}).get("value") is not None]
    known_def = [v for v in all_vehicles if v.get("def_level", {}).get("value") is not None]
    avg_fuel = round(sum(v["fuel"]["value"] for v in known_fuel) / len(known_fuel), 1) if known_fuel else _NA
    avg_def = round(sum(v["def_level"]["value"] for v in known_def) / len(known_def), 1) if known_def else _NA
    critical = sum(1 for v in all_vehicles if (v.get("fuel", {}).get("value") or 999) <= 15)
    low = sum(1 for v in all_vehicles if 15 < (v.get("fuel", {}).get("value") or 999) <= 30)
    good = sum(1 for v in all_vehicles if (v.get("fuel", {}).get("value") or 0) > 30)
    no_data = sum(1 for v in all_vehicles if v.get("fuel", {}).get("value") is None)
    writer.writerow([])
    writer.writerow([
        "SUMMARY", f"{len(all_vehicles)} trucks",
        f"Avg: {avg_fuel}%", f"Avg: {avg_def}%",
        f"{critical} Crit / {low} Low / {good} Good / {no_data} N/A",
        "", "", "", "", "", "",
    ])

    return _to_buf(sio)


# ══════════════════════════════════════════════════════════════════
# VEHICLE HEALTH CSV
# ══════════════════════════════════════════════════════════════════

def generate_health_csv(
    vehicles: list[dict],
    company_filter: str | None = None,
) -> io.BytesIO:
    """Return a BytesIO containing CSV data for the Vehicle Health report."""
    sio = io.StringIO()
    writer = csv.writer(sio)

    # Metadata
    writer.writerow(["Report", "Vehicle Health"])
    writer.writerow(["Generated", _now_et()])
    writer.writerow(["Company", company_filter or "All Companies"])
    writer.writerow([])

    header = [
        "Truck",
        "Company",
        "Engine",
        "Battery (V)",
        "Oil (PSI)",
        "Coolant (°C)",
        "DEF (%)",
        "Load (%)",
        "Seatbelt",
        "RPM",
        "Alerts",
        "Last Reading",
    ]
    writer.writerow(header)

    # Sort: alerts first, then company → truck
    sorted_v = sorted(
        vehicles,
        key=lambda x: (-len(x.get("_health_alerts", [])), x.get("_org", ""), x.get("name", "")),
    )

    for v in sorted_v:
        h = v.get("_health", {})
        alerts = v.get("_health_alerts", [])

        # Compute last reading relative time
        lr = _NA
        time_keys = [k for k in h if k.endswith("_time")]
        if time_keys:
            latest = max((h[k] for k in time_keys if h.get(k)), default="")
            if latest:
                lr = _fmt_iso_et(latest)

        writer.writerow([
            v.get("name", _NA),
            v.get("_org", _NA),
            "ON" if h.get("engine_on") else "OFF",
            _val(h.get("battery_v")),
            _val(h.get("oil_psi")),
            _val(h.get("coolant_c")),
            _val(h.get("def_pct")),
            _val(h.get("load_pct")),
            _val(h.get("seatbelt")),
            _val(h.get("rpm")),
            ", ".join(alerts) if alerts else "None",
            lr,
        ])

    # Summary row
    alert_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    with_alerts = sum(1 for v in vehicles if v.get("_health_alerts"))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    batts = [v["_health"]["battery_v"] for v in vehicles if v.get("_health", {}).get("battery_v") is not None]
    avg_batt = round(sum(batts) / len(batts), 2) if batts else _NA
    writer.writerow([])
    writer.writerow([
        "SUMMARY", f"{len(vehicles)} trucks",
        f"{eng_on} ON / {len(vehicles) - eng_on} OFF",
        f"Avg: {avg_batt}V" if batts else _NA,
        "", "", "", "", "",
        f"{with_alerts} with alerts ({alert_count} total)", "", "",
    ])

    return _to_buf(sio)


# ══════════════════════════════════════════════════════════════════
# FAULT REPORT CSV (one row per DTC)
# ══════════════════════════════════════════════════════════════════

def generate_fault_csv(
    vehicles_with_faults: list[dict],
    total_vehicles: int,
    company_filter: str | None = None,
) -> io.BytesIO:
    """Return a BytesIO containing CSV data for the Fault report.

    One row per DTC for easy filtering/pivoting in Excel.
    """
    sio = io.StringIO()
    writer = csv.writer(sio)

    total_dtcs = sum(len(v.get("_dtcs", [])) for v in vehicles_with_faults)

    # Metadata
    writer.writerow(["Report", "Fleet Fault Codes"])
    writer.writerow(["Generated", _now_et()])
    writer.writerow(["Company", company_filter or "All Companies"])
    writer.writerow(["Trucks Scanned", total_vehicles])
    writer.writerow(["Trucks with Faults", len(vehicles_with_faults)])
    writer.writerow(["Total DTCs", total_dtcs])
    writer.writerow([])

    header = [
        "Truck",
        "Company",
        "DTC Code",
        "Description",
        "Source",
        "SPN",
        "FMI",
        "FMI Description",
        "Occurrences",
        "STOP Light",
        "PROTECT Light",
        "EMISSIONS Light",
        "Fault Time",
        "VIN",
    ]
    writer.writerow(header)

    # Sort by severity: STOP first, then PROTECT, EMISSIONS, then rest
    def _sev_key(v):
        lights = v.get("_lights", {})
        return (
            not lights.get("stopIsOn", False),
            not lights.get("protectIsOn", False),
            not lights.get("emissionsIsOn", False),
            v.get("_org", ""),
            v.get("name", ""),
        )

    sorted_v = sorted(vehicles_with_faults, key=_sev_key)

    for v in sorted_v:
        lights = v.get("_lights", {})
        stop = "Yes" if lights.get("stopIsOn") else "No"
        protect = "Yes" if lights.get("protectIsOn") else "No"
        emissions = "Yes" if lights.get("emissionsIsOn") else "No"
        fault_time = _fmt_iso_et(v.get("_fault_time"))

        for dtc in v.get("_dtcs", []):
            writer.writerow([
                v.get("name", _NA),
                v.get("_org", _NA),
                dtc.get("code", _NA),
                dtc.get("description", _NA),
                dtc.get("source", _NA),
                _val(dtc.get("spnNumber")),
                _val(dtc.get("fmiCode")),
                dtc.get("fmiDescription", _NA),
                _val(dtc.get("occurrence")),
                stop,
                protect,
                emissions,
                fault_time,
                v.get("vin", _NA),
            ])

    # Summary row
    stop_count = sum(1 for v in vehicles_with_faults if v.get("_lights", {}).get("stopIsOn"))
    prot_count = sum(1 for v in vehicles_with_faults if v.get("_lights", {}).get("protectIsOn"))
    emis_count = sum(1 for v in vehicles_with_faults if v.get("_lights", {}).get("emissionsIsOn"))
    writer.writerow([])
    writer.writerow([
        "SUMMARY",
        f"{len(vehicles_with_faults)} faulted / {total_vehicles} total",
        f"{total_dtcs} DTCs", "", "", "", "", "",
        "", f"{stop_count} STOP", f"{prot_count} PROTECT",
        f"{emis_count} EMISSIONS", "", "",
    ])

    return _to_buf(sio)


# ══════════════════════════════════════════════════════════════════
# EVENTS CSV
# ══════════════════════════════════════════════════════════════════

def generate_events_csv(
    events: list[dict],
    days: int = 7,
    company_filter: str | None = None,
) -> io.BytesIO:
    """Return a BytesIO containing CSV data for the Events report."""
    sio = io.StringIO()
    writer = csv.writer(sio)

    # Metadata rows
    writer.writerow(["Report", "Safety Events"])
    writer.writerow(["Generated", _now_et()])
    writer.writerow(["Period", f"Past {days} Days"])
    writer.writerow(["Company", company_filter or "All Companies"])
    writer.writerow(["Total Events", len(events)])
    writer.writerow([])  # blank separator

    header = [
        "Date/Time",
        "Company",
        "Vehicle",
        "VIN",
        "Driver",
        "Event Type",
        "G-Force",
        "Latitude",
        "Longitude",
        "Coaching Status",
        "Video URL",
    ]
    writer.writerow(header)

    for e in events:
        writer.writerow([
            _fmt_iso_et(e.get("time")),
            e.get("_org", _NA),
            e.get("vehicle_name", _NA),
            e.get("vehicle_vin") or _NA,
            e.get("driver_name", _NA),
            e.get("event_name", _NA),
            e.get("g_force", _NA),
            e.get("latitude", _NA),
            e.get("longitude", _NA),
            e.get("coaching_state", _NA),
            e.get("video_url") or _NA,
        ])

    # Summary row
    from collections import Counter
    type_counts = Counter(e.get("event_name", "?") for e in events)
    summary_parts = [f"{name}: {cnt}" for name, cnt in type_counts.most_common()]
    writer.writerow([])
    writer.writerow(["SUMMARY", f"{len(events)} events", " | ".join(summary_parts)])

    buf = _to_buf(sio)
    from datetime import datetime as _dt
    ts = _dt.now(_TZ_ET).strftime("%Y%m%d_%H%M%S")
    buf.name = f"events_{ts}.csv"
    return buf
