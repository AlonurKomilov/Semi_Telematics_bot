"""Shared internal helpers for all formatter submodules."""

from datetime import datetime, timezone, timedelta
from typing import Optional
from constants import TZ_ET as _TZ_ET, TZ_CT as _TZ_CT, TZ_MT as _TZ_MT, TZ_PT as _TZ_PT

from samsara_client import COMPANY_DISPLAY


def _t(key: str, lang: str | None = None) -> str:
    """Lazy wrapper for bot.i18n.t() to avoid circular imports."""
    from bot.i18n import t
    return t(key, lang)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        return et.strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso_str


def _light_badges(lights: dict) -> str:
    badges = []
    if lights.get("stopIsOn"):
        badges.append("🛑 STOP")
    if lights.get("protectIsOn"):
        badges.append("🛡 PROTECT")
    if lights.get("emissionsIsOn"):
        badges.append("♨️ EMIS")
    if lights.get("warningIsOn"):
        badges.append("⚠️ WARN")
    return "  ".join(badges) if badges else _t('common.all_clear')


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


def _company_tag(v: dict, show_company: bool) -> str:
    """Return '[PTG] ' prefix when multi-company context."""
    if not show_company:
        return ""
    co = v.get("_org", "")
    return f"[{co}] " if co else ""


def _fmt_us_times(iso_str: str) -> str:
    """Convert an ISO timestamp to a compact multi-zone US display.

    Returns e.g. '03-20 06:01 ET / 05:01 CT / 04:01 MT / 03:01 PT'
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        ct = dt.astimezone(_TZ_CT)
        mt = dt.astimezone(_TZ_MT)
        pt = dt.astimezone(_TZ_PT)
        # Date from ET, then times for each zone
        date_prefix = et.strftime("%m-%d")
        return (
            f"{date_prefix} {et.strftime('%I:%M%p')} ET / "
            f"{ct.strftime('%I:%M%p')} CT / "
            f"{mt.strftime('%I:%M%p')} MT / "
            f"{pt.strftime('%I:%M%p')} PT"
        )
    except Exception:
        return iso_str[:16].replace("T", " ") if len(iso_str) > 16 else iso_str


def _engine_bar(driving_pct: int | float) -> str:
    """Build a visual bar showing driving vs idle split."""
    filled = round(driving_pct / 10)
    bar = "▓" * filled + "░" * (10 - filled)
    return bar


def _health_icon(alerts: list[str]) -> str:
    if not alerts:
        return "✅"
    if any(a in alerts for a in ("low_battery", "low_oil_pressure", "high_coolant_temp")):
        return "🔴"
    if any(a in alerts for a in ("low_def",)):
        return "🟡"
    return "⚠️"


def _temp_icon(temp_f: float | None) -> str:
    if temp_f is None:
        return "🌡"
    if temp_f <= 32:
        return "❄️"
    if temp_f >= 100:
        return "🔥"
    return "🌡"
