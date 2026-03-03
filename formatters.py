"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.
"""

from datetime import datetime, timezone
from typing import Optional


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso_str


def _light_badges(lights: dict) -> str:
    """Return compact colored badges for dash lights."""
    badges = []
    if lights.get("stopIsOn"):
        badges.append("🛑 STOP")
    if lights.get("protectIsOn"):
        badges.append("🛡 PROTECT")
    if lights.get("emissionsIsOn"):
        badges.append("♨️ EMIS")
    if lights.get("warningIsOn"):
        badges.append("⚠️ WARN")
    return "  ".join(badges) if badges else "✅ All Clear"


def _severity_rank(vehicle: dict) -> int:
    lights = vehicle.get("_lights", {})
    if lights.get("stopIsOn"):
        return 0
    if lights.get("protectIsOn"):
        return 1
    if lights.get("emissionsIsOn"):
        return 2
    if lights.get("warningIsOn"):
        return 3
    return 4


def _short_location(loc: dict) -> str:
    if not loc:
        return "—"
    reverse = loc.get("reverseGeo", {})
    addr = reverse.get("formattedLocation", "")
    if addr:
        parts = [p.strip() for p in addr.split(",")]
        if len(parts) >= 3:
            return f"{parts[-3]}, {parts[-2].strip()}"
        if len(parts) >= 2:
            return f"{parts[0]}, {parts[1].strip()}"
        return addr
    return "—"


def _fuel_bar(pct) -> str:
    """Visual fuel gauge bar."""
    if pct is None:
        return "⛽ —"
    filled = round(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    if pct <= 15:
        return f"🔴 {bar} {pct}%"
    if pct <= 25:
        return f"🟡 {bar} {pct}%"
    return f"🟢 {bar} {pct}%"


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ═══════════════════════════════════════════════════════════════════
#  /start  and  /help
# ═══════════════════════════════════════════════════════════════════

def format_help() -> str:
    return (
        "╔══════════════════════════╗\n"
        "     🚛  <b>Semi Telematics</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "Real-time fleet monitoring\n"
        "powered by Samsara\n"
        "\n"
        "▸ <b>Use the buttons below</b>\n"
        "  or type any command:\n"
        "\n"
        "  /faults    — Fault report (PDF)\n"
        "  /critical  — Critical faults (PDF)\n"
        "  /truck 134 — Single truck detail\n"
        "  /fuel      — Low fuel alert\n"
        "  /alerts    — Auto-notify\n"
    )


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
#  /truck <number>  —  Single Truck Detail
# ═══════════════════════════════════════════════════════════════════

def format_truck_detail(v: dict) -> list[str]:
    fc = v.get("fault_codes", {})
    j1939 = fc.get("j1939", {})
    dtcs = j1939.get("diagnosticTroubleCodes", [])
    lights = j1939.get("checkEngineLights", {})
    loc = v.get("location", {})
    fuel = v.get("fuel", {})
    fuel_pct = fuel.get("value")

    lines = [
        "┌─────────────────────────┐",
        f"  🚛  <b>TRUCK  #{v['name']}</b>",
        "└─────────────────────────┘",
        "",
        f"  {v['year']}  {v['make']}",
        f"  {v['model']}",
        f"  VIN    <code>{v['vin']}</code>",
        f"  Plate  {v.get('license_plate') or '—'}",
        "",
        f"  📍  {_short_location(loc)}",
        f"  {_fuel_bar(fuel_pct)}",
        "",
        f"  💡 Dash:  {_light_badges(lights)}",
    ]

    lines.append("")
    if not dtcs:
        lines.append("  ── ── ── ── ── ── ──")
        lines.append("  ✅  <b>No active fault codes</b>")
        lines.append("  Truck is running clean!")
    else:
        lines.append(f"  ── ── ── ── ── ── ──")
        lines.append(f"  🔧  <b>{len(dtcs)} Active Fault{'s' if len(dtcs) != 1 else ''}</b>")

        for i, dtc in enumerate(dtcs, 1):
            spn = dtc.get("spnId", "?")
            fmi = dtc.get("fmiId", "?")
            spn_desc = dtc.get("spnDescription", "Unknown")
            fmi_desc = dtc.get("fmiDescription", "Unknown")
            occ = dtc.get("occurrenceCount", 0)
            source = dtc.get("sourceAddressName", "Unknown")

            fmi_lower = fmi_desc.lower()
            if "most severe" in fmi_lower:
                sev = "🔴"
            elif "moderate" in fmi_lower:
                sev = "🟠"
            elif "least severe" in fmi_lower:
                sev = "🟡"
            else:
                sev = "⚪"

            lines.append(
                f"\n  {sev}  <b>Fault #{i}</b>\n"
                f"     Code    SPN {spn} / FMI {fmi}\n"
                f"     Issue   {spn_desc}\n"
                f"     Level   {fmi_desc}\n"
                f"     Count   ×{occ}\n"
                f"     From    {source}"
            )

    fault_time = fc.get("time", "")
    if fault_time:
        lines.append(f"\n  🕐  Updated:  {_fmt_time(fault_time)}")

    return _split_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  /fuel  —  Low Fuel Alert
# ═══════════════════════════════════════════════════════════════════

def format_low_fuel(low_fuel_vehicles: list, threshold: int) -> str:
    if not low_fuel_vehicles:
        return (
            "┌─────────────────────────┐\n"
            "  ✅  <b>FUEL OK</b>\n"
            "└─────────────────────────┘\n"
            f"\n  All trucks above {threshold}% fuel."
        )

    lines = [
        "┌─────────────────────────┐",
        "  ⛽  <b>LOW FUEL ALERT</b>",
        "└─────────────────────────┘",
        "",
        f"  {len(low_fuel_vehicles)} truck{'s' if len(low_fuel_vehicles) != 1 else ''} below {threshold}%",
        "",
    ]

    for v in low_fuel_vehicles:
        pct = v.get("_fuel_pct", 0)
        name = v["name"]
        loc = v.get("location", {})
        city = _short_location(loc)
        bar = _fuel_bar(pct)

        lines.append(
            f"  <b>#{name}</b>  ·  📍 {city}\n"
            f"  {bar}\n"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Auto-Alert  —  New Fault Notification
# ═══════════════════════════════════════════════════════════════════

def format_new_fault_alert(vehicle: dict, new_dtcs: list) -> str:
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)

    lines = [
        "╔══════════════════════════╗",
        "  🚨  <b>NEW FAULT DETECTED</b>",
        "╚══════════════════════════╝",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        "",
    ]

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        severity = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  🔸  <b>{desc}</b>\n"
            f"       {severity}\n"
            f"       Source: {source}\n"
        )

    lines.append(f"  ▸  /truck {name}  for full details")
    return "\n".join(lines)
