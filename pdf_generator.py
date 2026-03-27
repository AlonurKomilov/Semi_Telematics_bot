"""
PDF Fault Report Generator — professional multi-company fleet fault code reports.
Uses ReportLab to build clean, visual PDF documents.

Two report types:
  • Full Fault Report   — all trucks with active faults
  • Critical Report     — only STOP / PROTECT / EMISSIONS trucks

Multi-company enhancements:
  • Company breakdown summary table (Company | Trucks | Faulted | DTCs)
  • Company-section banners grouping trucks by company
  • Per-company or combined reports

Improvements (v2):
  M1  Dashboard stat cells — proper padding, two-row layout
  M2  Wider Severity + Source columns
  M3  Per-DTC row color tinting by fmiDescription severity
  M4  Smarter "Manufacturer Assignable SPN" → "MFR SPN XXXXX"
  N1  Section headers show truck + DTC counts
  N2  Page numbers in footer
  N3  FMI fallback: "FMI 31 (no description)"
  N4  Bold SPN/FMI codes in DTC table
  N5  Severity dot indicator in # column
"""

import io
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from reportlab.lib import colors

_TZ_ET = ZoneInfo("America/New_York")
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak, Image as RLImage,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.pdfgen.canvas import Canvas

from samsara_client import COMPANY_DISPLAY


# ── Color Palette ────────────────────────────────────────────────

C_DARK      = colors.HexColor("#1a1a2e")
C_HEADER_BG = colors.HexColor("#16213e")
C_ACCENT    = colors.HexColor("#0f3460")
C_RED       = colors.HexColor("#e94560")
C_ORANGE    = colors.HexColor("#f59e0b")
C_YELLOW    = colors.HexColor("#eab308")
C_GREEN     = colors.HexColor("#22c55e")
C_GRAY      = colors.HexColor("#64748b")
C_LIGHT_BG  = colors.HexColor("#f1f5f9")
C_WHITE     = colors.white
C_BLACK     = colors.black

# Truck-level section colors
SEV_STOP    = colors.HexColor("#fde2e4")
SEV_PROTECT = colors.HexColor("#fde2e4")
SEV_EMIS    = colors.HexColor("#fff3cd")
SEV_WARN    = colors.HexColor("#fff9db")
SEV_MINOR   = colors.HexColor("#e8f5e9")

# Per-DTC row tint colors (M3)
ROW_MOST_SEVERE = colors.HexColor("#fde2e4")   # light red
ROW_MODERATE    = colors.HexColor("#fff0e0")   # light orange
ROW_LEAST       = colors.HexColor("#fefce8")   # light yellow
ROW_FAILURE     = colors.HexColor("#fde2e4")   # light red (same as most severe)
ROW_DEFAULT     = C_WHITE                       # white — data error, etc.
ROW_ALT         = C_LIGHT_BG                    # alternate band
C_MINOR_BG      = colors.HexColor("#eef2ff")    # light indigo — minor faults

# Severity dot colors for the # column (N5)
DOT_RED    = "#e94560"
DOT_ORANGE = "#f59e0b"
DOT_YELLOW = "#eab308"
DOT_GRAY   = "#94a3b8"

# Company-banner colors
C_ORG_BANNER = colors.HexColor("#1e3a5f")

# Critical report accent colors
C_CRIT_HEADER = colors.HexColor("#7f1d1d")    # dark red header
C_CRIT_BANNER = colors.HexColor("#991b1b")    # red company banner


# ── Helpers ──────────────────────────────────────────────────────

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "\u2014"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        return et.strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso_str


def _fmt_time_with_age(iso_str: str) -> str:
    """Format timestamp with a colour-coded age badge like '(2h ago)' or '(3d ago!)'."""
    if not iso_str:
        return "\u2014"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        base = et.strftime("%b %d, %Y  %I:%M %p")
        now = datetime.now(_TZ_ET)
        delta = now - et
        total_min = int(delta.total_seconds() / 60)
        if total_min < 0:
            return base
        if total_min < 60:
            age = f"{total_min}m ago"
            clr = "#22c55e"        # green — fresh
        elif total_min < 1440:     # < 24 h
            age = f"{total_min // 60}h ago"
            clr = "#22c55e" if total_min < 360 else "#f59e0b"  # green < 6h, amber otherwise
        else:
            days = total_min // 1440
            age = f"{days}d ago!"
            clr = "#e94560"        # red — stale
        return f'{base}  <font color="{clr}"><b>({age})</b></font>'
    except Exception:
        return iso_str


def _occ_trend(occ_val) -> str:
    """Return a trend arrow based on occurrence count thresholds."""
    if occ_val is None or occ_val == "":
        return "\u2014"
    try:
        count = int(occ_val)
    except (ValueError, TypeError):
        return str(occ_val)
    if count >= 50:
        return f'<font color="#e94560">\u25b2\u25b2</font> {count}'   # ▲▲ red — very frequent
    if count >= 10:
        return f'<font color="#f59e0b">\u25b2</font> {count}'         # ▲ amber — frequent
    if count >= 2:
        return str(count)
    return f'<font color="#22c55e">\u25bc</font> {count}'             # ▼ green — rare


def _short_location(loc: dict) -> str:
    if not loc:
        return "\u2014"
    reverse = loc.get("reverseGeo", {})
    addr = reverse.get("formattedLocation", "")
    if addr:
        parts = [p.strip() for p in addr.split(",")]
        if len(parts) >= 3:
            return f"{parts[-3]}, {parts[-2].strip()}"
        if len(parts) >= 2:
            return f"{parts[0]}, {parts[1].strip()}"
        return addr
    return "\u2014"


def _severity_label(lights: dict) -> tuple[str, colors.Color]:
    if lights.get("stopIsOn"):
        return "STOP", SEV_STOP
    if lights.get("protectIsOn"):
        return "PROTECT", SEV_PROTECT
    if lights.get("emissionsIsOn"):
        return "EMISSIONS", SEV_EMIS
    if lights.get("warningIsOn"):
        return "WARNING", SEV_WARN
    return "MINOR", SEV_MINOR


def _light_badges_text(lights: dict) -> str:
    badges = []
    if lights.get("stopIsOn"):
        badges.append("STOP")
    if lights.get("protectIsOn"):
        badges.append("PROTECT")
    if lights.get("emissionsIsOn"):
        badges.append("EMISSIONS")
    if lights.get("warningIsOn"):
        badges.append("WARNING")
    return " / ".join(badges) if badges else "Clear"


def _sev_rank(v: dict) -> int:
    lights = v.get("_lights", {})
    if lights.get("stopIsOn"):
        return 0
    if lights.get("protectIsOn"):
        return 1
    if lights.get("emissionsIsOn"):
        return 2
    if lights.get("warningIsOn"):
        return 3
    return 4


def _safe(val, fallback: str = "\u2014") -> str:
    if val is None or val == "":
        return fallback
    return str(val)


# ── M4: Smart SPN description ───────────────────────────────────

def _spn_display(dtc: dict) -> str:
    desc = dtc.get("spnDescription", "")
    spn_id = dtc.get("spnId", "?")
    if not desc:
        if spn_id and spn_id != 0:
            return f"SPN {spn_id} (no description)"
        return "Unknown Component"
    if desc.strip().lower() == "manufacturer assignable spn":
        return f"MFR SPN {spn_id}"
    return desc


# ── N3: Smart FMI description ───────────────────────────────────

def _fmi_display(dtc: dict) -> str:
    desc = dtc.get("fmiDescription", "")
    fmi_id = dtc.get("fmiId", "?")
    if not desc:
        return f"FMI {fmi_id} (no description)"
    return desc


# ── M3 / N5: Per-DTC severity from fmiDescription ───────────────

def _dtc_severity_info(fmi_desc: str) -> tuple[colors.Color, str, str]:
    lower = (fmi_desc or "").lower()
    if "most severe" in lower:
        return ROW_MOST_SEVERE, DOT_RED, "\u25cf"
    if "failure" in lower:
        return ROW_FAILURE, DOT_RED, "\u25cf"
    if "moderate" in lower:
        return ROW_MODERATE, DOT_ORANGE, "\u25cf"
    if "least severe" in lower:
        return ROW_LEAST, DOT_YELLOW, "\u25cf"
    return ROW_DEFAULT, DOT_GRAY, "\u25cb"


# ── Styles ───────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()

    def _add(name, **kw):
        base.add(ParagraphStyle(name, **kw))

    _add("ReportTitle",    parent=base["Title"],   fontName="Helvetica-Bold",
         fontSize=22, textColor=C_WHITE, alignment=TA_CENTER, spaceAfter=4)
    _add("ReportSubtitle", parent=base["Normal"],  fontName="Helvetica",
         fontSize=10, textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER,
         spaceAfter=2)
    _add("SectionHeader",  parent=base["Heading2"], fontName="Helvetica-Bold",
         fontSize=12, textColor=C_WHITE, spaceBefore=14, spaceAfter=6)
    _add("CompanyBanner",      parent=base["Heading2"], fontName="Helvetica-Bold",
         fontSize=11, textColor=C_WHITE, spaceBefore=10, spaceAfter=4)
    _add("TruckTitle",     parent=base["Heading3"], fontName="Helvetica-Bold",
         fontSize=11, textColor=C_DARK, spaceBefore=8, spaceAfter=3)
    _add("TruckMeta",      parent=base["Normal"],  fontName="Helvetica",
         fontSize=8.5, textColor=C_GRAY, spaceAfter=2, leading=11)
    _add("CellText",       parent=base["Normal"],  fontName="Helvetica",
         fontSize=8, textColor=C_BLACK, leading=10)
    _add("CellBold",       parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=8, textColor=C_BLACK, leading=10)
    _add("FooterText",     parent=base["Normal"],  fontName="Helvetica",
         fontSize=8, textColor=C_GRAY, alignment=TA_CENTER)
    _add("StatNumber",     parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=16, alignment=TA_CENTER, leading=20, spaceBefore=0, spaceAfter=0)
    _add("StatLabel",      parent=base["Normal"],  fontName="Helvetica",
         fontSize=7.5, textColor=C_GRAY, alignment=TA_CENTER,
         leading=9, spaceBefore=0, spaceAfter=0)
    _add("OrgTableHeader", parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=8, textColor=C_WHITE, leading=10)
    _add("CompanyTableCell",   parent=base["Normal"],  fontName="Helvetica",
         fontSize=8, textColor=C_BLACK, leading=10)
    _add("OrgTableBold",   parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=8, textColor=C_BLACK, leading=10)

    return base


# ── Stat helpers (for caption) ───────────────────────────────────

def compute_stats(vehicles_with_faults: list, total_vehicles: int) -> dict:
    """Compute summary statistics used by both PDF and Telegram caption."""
    stop = protect = emis = warn = minor = 0
    total_dtcs = 0

    for v in vehicles_with_faults:
        lights = v.get("_lights", {})
        dtcs = v.get("_dtcs", [])
        total_dtcs += len(dtcs)
        if lights.get("stopIsOn"):
            stop += 1
        elif lights.get("protectIsOn"):
            protect += 1
        elif lights.get("emissionsIsOn"):
            emis += 1
        elif lights.get("warningIsOn"):
            warn += 1
        else:
            minor += 1

    return {
        "total":       total_vehicles,
        "faulted":     len(vehicles_with_faults),
        "clean":       total_vehicles - len(vehicles_with_faults),
        "total_dtcs":  total_dtcs,
        "stop":        stop,
        "protect":     protect,
        "emissions":   emis,
        "warning":     warn,
        "minor":       minor,
        "critical":    stop + protect + emis,
    }


# Unique style counter to avoid ReportLab name collisions (uses id() per call)


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate full fault report
# ══════════════════════════════════════════════════════════════════

def generate_fault_report_pdf(
    vehicles_with_faults: list,
    total_vehicles: int,
    company_breakdown: dict[str, dict] | None = None,
    company_filter: str | None = None,
    all_vehicles: list | None = None,
) -> io.BytesIO:
    """Generate PDF fault report with optional fleet overview.

    Args:
        vehicles_with_faults: List of vehicle dicts (each has ``_org`` key).
        total_vehicles: Grand total of active vehicles scanned.
        company_breakdown: Per-company stats {code: {total, faulted, dtcs}}.
        company_filter: If set, this is a single-company report.
        all_vehicles: Full fleet list for fleet overview pages.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")
    stats = compute_stats(vehicles_with_faults, total_vehicles)

    # ── Header Banner ────────────────────────────────────────────
    subtitle = "Fleet Fault Code Report"
    if company_filter:
        subtitle = f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — Fault Report"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Fleet Overview (when full fleet data is available) ────────
    if all_vehicles:
        _add_fleet_health_overview(story, styles, all_vehicles, stats)
        if company_breakdown and not company_filter and len(company_breakdown) > 1:
            _add_company_breakdown_table(story, styles, company_breakdown)
        show_company_grid = (
            len(set(v.get("_org", "") for v in all_vehicles)) > 1
            and not company_filter
        )
        _add_fleet_status_grid(story, styles, all_vehicles,
                               show_org=show_company_grid)
        if vehicles_with_faults:
            story.append(PageBreak())
    else:
        _add_summary_dashboard(story, styles, stats)
        if company_breakdown and not company_filter and len(company_breakdown) > 1:
            _add_company_breakdown_table(story, styles, company_breakdown)

    # ── Truck cards grouped by company then severity ─────────────────
    vehicles_with_faults = sorted(vehicles_with_faults, key=_sev_rank)

    # Group by company
    companies_present = []
    if company_filter:
        companies_present = [company_filter]
    else:
        seen = []
        for v in vehicles_with_faults:
            o = v.get("_org", "???")
            if o not in seen:
                seen.append(o)
        companies_present = seen

    multi_org = len(companies_present) > 1

    # ── Table of Contents (when 4+ trucks) ───────────────────────
    if len(vehicles_with_faults) >= 4:
        story.extend(_build_toc(styles, vehicles_with_faults,
                                show_org=multi_org))

    for co_code in companies_present:
        co_vehicles = [v for v in vehicles_with_faults if v.get("_org") == co_code]
        if not co_vehicles:
            continue

        # Each company section starts on a fresh page
        story.append(PageBreak())

        # Company banner (only for multi-company combined reports)
        if multi_org:
            co_name = COMPANY_DISPLAY.get(co_code, co_code)
            co_dtcs = sum(len(v.get("_dtcs", [])) for v in co_vehicles)
            _add_company_banner(story, styles, co_code, co_name,
                            len(co_vehicles), co_dtcs)

        for v in co_vehicles:
            story.extend(_build_truck_card(v, styles, show_org=multi_org))
            story.append(Spacer(1, 10))

    # ── Action Items (STOP / PROTECT / EMISSIONS trucks) ─────────
    story.extend(_build_action_items(styles, vehicles_with_faults))

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate critical-only report
# ══════════════════════════════════════════════════════════════════

def generate_critical_report_pdf(
    critical_vehicles: list,
    total_vehicles: int,
    company_breakdown: dict[str, dict] | None = None,
    company_filter: str | None = None,
) -> io.BytesIO:
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")

    subtitle = "Critical Fault Report"
    if company_filter:
        subtitle = f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — Critical Faults"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now,
                header_bg=C_CRIT_HEADER)

    # ── Critical alert stripe ────────────────────────────────────
    page_w = 7.1 * inch
    alert_text = (
        "\u26a0  CRITICAL FAULTS ONLY \u2014 "
        "Immediate Attention Required"
    )
    alert_style = ParagraphStyle(
        "CritAlert", parent=styles["CompanyBanner"],
        fontSize=9, leading=12,
    )
    alert_tbl = Table(
        [[Paragraph(alert_text, alert_style)]],
        colWidths=[page_w],
    )
    alert_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_RED),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(alert_tbl)
    story.append(Spacer(1, 10))

    # ── Mini summary ─────────────────────────────────────────────
    n_crit = len(critical_vehicles)
    stop = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("stopIsOn"))
    protect = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("protectIsOn"))
    emis = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("emissionsIsOn"))
    total_dtcs = sum(len(v.get("_dtcs", [])) for v in critical_vehicles)

    health_pct = round((1 - n_crit / total_vehicles) * 100) if total_vehicles else 0
    health_color = C_GREEN if health_pct >= 80 else (C_ORANGE if health_pct >= 60 else C_RED)

    crit_summary = [[
        _mini_stat(styles, f"{n_crit} / {total_vehicles}",
                   "Critical Trucks", C_RED),
        _mini_stat(styles, f"{health_pct}%", "Fleet Health", health_color),
        _mini_stat(styles, str(total_dtcs), "Fault Codes", C_ORANGE),
        _mini_stat(styles, str(stop), "STOP", C_RED),
        _mini_stat(styles, str(protect), "PROTECT", C_ORANGE),
        _mini_stat(styles, str(emis), "EMISSIONS", C_YELLOW),
    ]]
    t = Table(crit_summary, colWidths=[page_w / 6] * 6)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # ── Company Breakdown Table (multi-company only) ─────────────────────
    if company_breakdown and not company_filter and len(company_breakdown) > 1:
        _add_company_breakdown_table(story, styles, company_breakdown)

    # ── Truck cards grouped by company ───────────────────────────────
    critical_vehicles = sorted(critical_vehicles, key=_sev_rank)

    companies_present = []
    seen = []
    for v in critical_vehicles:
        o = v.get("_org", "???")
        if o not in seen:
            seen.append(o)
    companies_present = seen
    multi_org = len(companies_present) > 1

    # ── Table of Contents (when 4+ trucks) ───────────────────────
    if len(critical_vehicles) >= 4:
        story.extend(_build_toc(styles, critical_vehicles,
                                show_org=multi_org))

    for co_code in companies_present:
        co_vehicles = [v for v in critical_vehicles if v.get("_org") == co_code]
        if not co_vehicles:
            continue

        # Each company section starts on a fresh page
        story.append(PageBreak())

        if multi_org:
            co_name = COMPANY_DISPLAY.get(co_code, co_code)
            co_dtcs = sum(len(v.get("_dtcs", [])) for v in co_vehicles)
            _add_company_banner(story, styles, co_code, co_name,
                            len(co_vehicles), co_dtcs,
                            banner_color=C_CRIT_BANNER)

        for v in co_vehicles:
            story.extend(_build_truck_card(v, styles, show_org=multi_org))
            story.append(Spacer(1, 10))

    # ── Action Items (STOP / PROTECT / EMISSIONS trucks) ─────────
    story.extend(_build_action_items(styles, critical_vehicles))

    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate single-truck detail report
# ══════════════════════════════════════════════════════════════════

def generate_truck_detail_pdf(vehicle: dict) -> io.BytesIO:
    """Generate a professional PDF for a single truck.

    Includes: vehicle overview with color-coded indicators,
    dashboard lights, location & fuel, gateway status,
    and a full fault-code table (if any DTCs exist).
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")

    co_code = vehicle.get("_org", "")
    co_name = COMPANY_DISPLAY.get(co_code, co_code)
    truck_name = vehicle.get("name", "?")

    # ── Header ───────────────────────────────────────────────────
    subtitle = f"Truck #{truck_name} — Detail Report"
    if co_code:
        subtitle = f"Truck #{truck_name} — {co_name} ({co_code})"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Gateway warning ──────────────────────────────────────────
    if not vehicle.get("has_gateway", True):
        gw_text = Paragraph(
            '<font color="#e94560">\u26a0\ufe0f  <b>No Samsara gateway device</b></font>'
            '  \u2014  This truck has no physical Samsara device installed. '
            'Data may be incomplete or unavailable.',
            styles["TruckMeta"],
        )
        story.append(gw_text)
        story.append(Spacer(1, 6))

    # ── Vehicle info table ───────────────────────────────────────
    page_w = 7.1 * inch
    fc = vehicle.get("fault_codes", {})
    j1939 = fc.get("j1939", {})
    dtcs = j1939.get("diagnosticTroubleCodes", [])
    lights = j1939.get("checkEngineLights", {})
    loc = vehicle.get("location", {})
    fuel = vehicle.get("fuel", {})
    fuel_pct = fuel.get("value")

    loc_str = _short_location(loc)
    lights_str = _light_badges_text(lights)

    # Color-coded fuel
    fuel_str = f"{fuel_pct}%" if fuel_pct is not None else "\u2014"
    fuel_hex = "#22c55e"
    if fuel_pct is None:
        fuel_hex = "#94a3b8"
    elif fuel_pct <= 15:
        fuel_hex = "#e94560"
    elif fuel_pct <= 30:
        fuel_hex = "#f59e0b"
    fuel_display = f'<font color="{fuel_hex}"><b>{fuel_str}</b></font>'

    # Color-coded dashboard lights
    dash_hex = "#22c55e" if lights_str == "Clear" else "#e94560"
    dash_display = f'<font color="{dash_hex}"><b>{lights_str}</b></font>'

    fc_time = fc.get("time", "")
    scan_str = _fmt_time_with_age(fc_time) if fc_time else "\u2014"

    info_rows = [
        ("Year / Make / Model",
         f"{_safe(vehicle.get('year'))}  {_safe(vehicle.get('make'))}  "
         f"{_safe(vehicle.get('model'))}"),
        ("VIN", f'<font face="Courier">{_safe(vehicle.get("vin"), "N/A")}</font>'),
        ("License Plate", _safe(vehicle.get("license_plate"), "N/A")),
        ("Location", loc_str),
        ("Fuel Level", fuel_display),
        ("Dashboard Lights", dash_display),
        ("Last Fault Scan", scan_str),
    ]

    info_table_data = []
    for label, val in info_rows:
        info_table_data.append([
            Paragraph(f"<b>{label}</b>", styles["CellBold"]),
            Paragraph(val, styles["CellText"]),
        ])

    t = Table(info_table_data, colWidths=[2.0 * inch, page_w - 2.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), C_LIGHT_BG),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    # ── Fault summary mini-dashboard ─────────────────────────────
    total_dtcs = len(dtcs)
    stop_on = 1 if lights.get("stopIsOn") else 0
    emis_on = 1 if lights.get("emissionsIsOn") else 0
    warn_on = 1 if lights.get("warningIsOn") else 0
    prot_on = 1 if lights.get("protectIsOn") else 0

    if total_dtcs:
        # Determine overall severity label + color
        sev_label, _ = _severity_label(lights)
        sev_color = C_RED if sev_label in ("STOP", "PROTECT") else (
            C_ORANGE if sev_label == "EMISSIONS" else (
            C_YELLOW if sev_label == "WARNING" else C_GREEN))
        row1 = [[
            _mini_stat(styles, str(total_dtcs), "Fault Codes", C_RED),
            _mini_stat(styles, sev_label, "Severity", sev_color),
            _mini_stat(styles, str(stop_on), "STOP", C_RED),
            _mini_stat(styles, str(prot_on), "PROTECT", C_ORANGE),
            _mini_stat(styles, str(emis_on), "EMISSIONS", C_YELLOW),
            _mini_stat(styles, str(warn_on), "WARNING", C_ORANGE),
        ]]
        t1 = Table(row1, colWidths=[page_w / 6] * 6, rowHeights=[52])
        t1.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
            ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
            ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t1)
        story.append(Spacer(1, 10))
    else:
        clean_text = Paragraph(
            '<font color="#22c55e"><b>\u2705  ALL CLEAR</b></font>'
            '  \u2014  No active fault codes.  Truck is running clean!',
            styles["TruckTitle"],
        )
        story.append(clean_text)
        story.append(Spacer(1, 10))

    # ── Fault code detail table ──────────────────────────────────
    if dtcs:
        _add_section_header(
            story, styles,
            f"FAULT CODES  \u2502  {total_dtcs} active DTC"
            f"{'s' if total_dtcs != 1 else ''}",
            C_RED if (stop_on or prot_on or emis_on) else C_ORANGE,
        )

        col_widths = [
            0.30 * inch,   # #
            0.55 * inch,   # SPN
            0.40 * inch,   # FMI
            2.40 * inch,   # Issue
            1.90 * inch,   # Severity
            0.45 * inch,   # Cnt
            1.10 * inch,   # Source
        ]
        hdr = [
            Paragraph("<b>#</b>",        styles["CellBold"]),
            Paragraph("<b>SPN</b>",      styles["CellBold"]),
            Paragraph("<b>FMI</b>",      styles["CellBold"]),
            Paragraph("<b>Issue</b>",    styles["CellBold"]),
            Paragraph("<b>Severity</b>", styles["CellBold"]),
            Paragraph("<b>Cnt</b>",      styles["CellBold"]),
            Paragraph("<b>Source</b>",   styles["CellBold"]),
        ]
        table_data = [hdr]
        row_severity_info = []

        for i, dtc in enumerate(dtcs, 1):
            spn_id   = _safe(dtc.get("spnId"), "?")
            fmi_id   = _safe(dtc.get("fmiId"), "?")
            issue    = _spn_display(dtc)
            severity = _fmi_display(dtc)
            occ      = dtc.get("occurrenceCount")
            source   = _safe(dtc.get("sourceAddressName"), "\u2014")

            row_bg, dot_hex, dot_sym = _dtc_severity_info(
                dtc.get("fmiDescription", "")
            )
            row_severity_info.append((row_bg, dot_hex, dot_sym))

            occ_display = _occ_trend(occ)

            num_cell = Paragraph(
                f'<font color="{dot_hex}">{dot_sym}</font> {i}',
                styles["CellText"],
            )
            table_data.append([
                num_cell,
                Paragraph(f"<b>{spn_id}</b>", styles["CellBold"]),
                Paragraph(f"<b>{fmi_id}</b>", styles["CellBold"]),
                Paragraph(issue,    styles["CellText"]),
                Paragraph(severity, styles["CellText"]),
                Paragraph(occ_display, styles["CellText"]),
                Paragraph(source,   styles["CellText"]),
            ])

        dtc_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
            ("ALIGN",         (0, 0), (0, -1), "CENTER"),
            ("ALIGN",         (1, 0), (2, -1), "CENTER"),
            ("ALIGN",         (5, 0), (5, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
        ]
        for idx, (bg_color, _, _) in enumerate(row_severity_info):
            row_num = idx + 1
            cmds.append(("BACKGROUND", (0, row_num), (-1, row_num), bg_color))
        dtc_table.setStyle(TableStyle(cmds))
        story.append(dtc_table)

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# SHARED COMPONENTS
# ══════════════════════════════════════════════════════════════════

class _NumberedCanvas(Canvas):
    """Canvas subclass that renders 'Page X of N' on every page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(total)
            super().showPage()
        super().save()

    def _draw_page_number(self, total: int):
        self.saveState()
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#94a3b8"))
        self.drawRightString(
            self._pagesize[0] - 0.5 * inch,
            0.35 * inch,
            f"Page {self._pageNumber} of {total}",
        )
        self.restoreState()


def _add_header(story, styles, title, subtitle, date_str, header_bg=None):
    page_w = 7.1 * inch
    bg = header_bg or C_HEADER_BG
    rows = [
        [Paragraph(title, styles["ReportTitle"])],
        [Paragraph(subtitle, styles["ReportSubtitle"])],
        [Paragraph(date_str, styles["ReportSubtitle"])],
    ]
    t = Table(rows, colWidths=[page_w])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), bg),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, 0), 16),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))


# ── M1: Fixed summary dashboard ─────────────────────────────────

def _add_summary_dashboard(story, styles, stats):
    page_w = 7.1 * inch

    row1 = [[
        _mini_stat(styles, str(stats["total"]),      "Total Trucks", C_ACCENT),
        _mini_stat(styles, str(stats["faulted"]),     "With Faults",  C_RED),
        _mini_stat(styles, str(stats["clean"]),       "Clean",        C_GREEN),
        _mini_stat(styles, str(stats["total_dtcs"]),  "Total DTCs",   C_ORANGE),
    ]]
    t1 = Table(row1, colWidths=[page_w / 4] * 4, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))

    row2 = [[
        _mini_stat(styles, str(stats["stop"]),      "STOP",      C_RED),
        _mini_stat(styles, str(stats["protect"]),    "PROTECT",   C_RED),
        _mini_stat(styles, str(stats["emissions"]),  "EMISSIONS", C_ORANGE),
        _mini_stat(styles, str(stats["warning"]),    "WARNING",   C_YELLOW),
        _mini_stat(styles, str(stats["minor"]),      "MINOR",     C_GREEN),
    ]]
    t2 = Table(row2, colWidths=[page_w / 5] * 5, rowHeights=[48])
    t2.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(KeepTogether([t1, Spacer(1, 4), t2]))
    story.append(Spacer(1, 10))


# ── Company Breakdown Table (multi-company) ─────────────────────────────

def _add_company_breakdown_table(story, styles, company_breakdown: dict[str, dict]):
    """Render a Company | Trucks | Faulted | DTCs summary table."""
    page_w = 7.1 * inch
    col_widths = [3.5 * inch, 1.2 * inch, 1.2 * inch, 1.2 * inch]

    hdr = [
        Paragraph("<b>Company</b>", styles["OrgTableHeader"]),
        Paragraph("<b>Trucks</b>",  styles["OrgTableHeader"]),
        Paragraph("<b>Faulted</b>", styles["OrgTableHeader"]),
        Paragraph("<b>DTCs</b>",    styles["OrgTableHeader"]),
    ]
    table_data = [hdr]

    grand_total = grand_faulted = grand_dtcs = 0
    for code in sorted(company_breakdown.keys()):
        info = company_breakdown[code]
        name = COMPANY_DISPLAY.get(code, code)
        total = info.get("total", 0)
        faulted = info.get("faulted", 0)
        dtcs = info.get("dtcs", 0)
        grand_total += total
        grand_faulted += faulted
        grand_dtcs += dtcs
        table_data.append([
            Paragraph(f"{name}  ({code})", styles["CompanyTableCell"]),
            Paragraph(str(total),  styles["CompanyTableCell"]),
            Paragraph(str(faulted), styles["CompanyTableCell"]),
            Paragraph(str(dtcs),   styles["CompanyTableCell"]),
        ])

    # Totals row
    table_data.append([
        Paragraph("<b>TOTAL</b>",          styles["OrgTableBold"]),
        Paragraph(f"<b>{grand_total}</b>",  styles["OrgTableBold"]),
        Paragraph(f"<b>{grand_faulted}</b>", styles["OrgTableBold"]),
        Paragraph(f"<b>{grand_dtcs}</b>",   styles["OrgTableBold"]),
    ])

    t = Table(table_data, colWidths=col_widths)
    num_rows = len(table_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("BACKGROUND",    (0, -1), (-1, -1), C_LIGHT_BG),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
    ]
    # Alternate row shading for data rows
    for i in range(1, num_rows - 1):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG))
    t.setStyle(TableStyle(cmds))
    story.append(t)
    story.append(Spacer(1, 12))


# ── Company Banner (multi-company) ──────────────────────────────────────

def _add_company_banner(story, styles, co_code, co_name, truck_count, dtc_count,
                    banner_color=None):
    """Dark blue banner with company name, code, and counts."""
    page_w = 7.1 * inch
    bg = banner_color or C_ORG_BANNER
    text = (
        f"  {co_name.upper()}  ({co_code})  \u2502  "
        f"{truck_count} truck{'s' if truck_count != 1 else ''}  \u00b7  "
        f"{dtc_count} fault code{'s' if dtc_count != 1 else ''}"
    )
    t = Table(
        [[Paragraph(text, styles["CompanyBanner"])]],
        colWidths=[page_w],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), bg),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 4))
    story.append(t)
    story.append(Spacer(1, 6))


def _add_section_header(story, styles, title, color):
    """Append a colored section header bar.  Returns the header element
    so callers can wrap it in KeepTogether with the subsequent table."""
    page_w = 7.1 * inch
    t = Table(
        [[Paragraph(f"  {title}", styles["SectionHeader"])]],
        colWidths=[page_w],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), color),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    return t


def _add_footer(story, styles, date_str):
    story.append(Spacer(1, 16))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=C_GRAY, spaceBefore=4, spaceAfter=8,
    ))
    story.append(Paragraph(
        f"Generated by Semi Telematics Bot  \u2022  {date_str}  \u2022  Data from Samsara API",
        styles["FooterText"],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "CONFIDENTIAL — This report contains proprietary fleet data. "
        "Do not distribute without authorization.",
        ParagraphStyle(
            "DisclaimerText", parent=styles["FooterText"],
            fontSize=6.5, textColor=C_GRAY, alignment=TA_CENTER,
        ),
    ))


# ── M1: Mini stat cell with proper spacing ──────────────────────

def _mini_stat(styles, value: str, label: str, accent: colors.Color):
    val_para = Paragraph(
        f'<font color="{accent.hexval()}">{value}</font>',
        ParagraphStyle(
            f"mv_{id(accent)}_{id(value)}", parent=styles["StatNumber"],
            textColor=accent,
        ),
    )
    lbl_para = Paragraph(
        label,
        ParagraphStyle(
            f"ml_{id(accent)}_{id(label)}", parent=styles["StatLabel"],
        ),
    )
    return [val_para, Spacer(1, 2), lbl_para]


def _build_toc(styles, vehicles: list, show_org: bool = False) -> list:
    """Build a compact Table of Contents listing every truck with severity + DTC count."""
    page_w = 7.1 * inch
    elements = []

    _add_section_header_raw = lambda title, color: _make_section_header_table(
        styles, title, color
    )
    hdr_tbl = _make_section_header_table(styles, "TABLE OF CONTENTS", C_ACCENT)
    elements.append(hdr_tbl)
    elements.append(Spacer(1, 6))

    if show_org:
        col_w = [0.30 * inch, 0.65 * inch, 0.80 * inch, 0.70 * inch,
                 0.50 * inch, 3.15 * inch, 1.00 * inch]
        toc_hdr = [
            Paragraph("<b>#</b>",        styles["CellBold"]),
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Company</b>",  styles["CellBold"]),
            Paragraph("<b>Status</b>",   styles["CellBold"]),
            Paragraph("<b>DTCs</b>",     styles["CellBold"]),
            Paragraph("<b>Top Issue</b>", styles["CellBold"]),
            Paragraph("<b>Vehicle</b>",  styles["CellBold"]),
        ]
    else:
        col_w = [0.30 * inch, 0.70 * inch, 0.75 * inch, 0.50 * inch,
                 3.45 * inch, 1.40 * inch]
        toc_hdr = [
            Paragraph("<b>#</b>",        styles["CellBold"]),
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Status</b>",   styles["CellBold"]),
            Paragraph("<b>DTCs</b>",     styles["CellBold"]),
            Paragraph("<b>Top Issue</b>", styles["CellBold"]),
            Paragraph("<b>Vehicle</b>",  styles["CellBold"]),
        ]

    toc_data = [toc_hdr]
    for i, v in enumerate(vehicles, 1):
        lights = v.get("_lights", {})
        dtcs = v.get("_dtcs", [])
        sev_label, _ = _severity_label(lights)
        sev_color_hex = (
            "#e94560" if sev_label in ("STOP", "PROTECT") else
            "#f59e0b" if sev_label == "EMISSIONS" else
            "#eab308" if sev_label == "WARNING" else "#94a3b8"
        )
        top_issue = _spn_display(dtcs[0]) if dtcs else "\u2014"
        veh = f"{_safe(v.get('year'))} {_safe(v.get('make'))}"

        row = [
            Paragraph(str(i), styles["CellText"]),
            Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"]),
        ]
        if show_org:
            row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
        row.extend([
            Paragraph(f'<font color="{sev_color_hex}"><b>{sev_label}</b></font>',
                      styles["CellBold"]),
            Paragraph(str(len(dtcs)), styles["CellText"]),
            Paragraph(top_issue, styles["CellText"]),
            Paragraph(veh, styles["CellText"]),
        ])
        toc_data.append(row)

    toc_table = Table(toc_data, colWidths=col_w, repeatRows=1)
    n = len(toc_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    for r in range(1, n):
        if r % 2 == 0:
            cmds.append(("BACKGROUND", (0, r), (-1, r), C_LIGHT_BG))
    toc_table.setStyle(TableStyle(cmds))
    elements.append(toc_table)
    elements.append(Spacer(1, 10))
    return elements


def _make_section_header_table(styles, title, color):
    """Create a section header table without appending to story."""
    page_w = 7.1 * inch
    t = Table(
        [[Paragraph(f"  {title}", styles["SectionHeader"])]],
        colWidths=[page_w],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), color),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _build_action_items(styles, vehicles: list) -> list:
    """Build an 'Action Items' summary table for STOP/PROTECT/EMISSIONS trucks."""
    critical = [
        v for v in vehicles
        if v.get("_lights", {}).get("stopIsOn")
        or v.get("_lights", {}).get("protectIsOn")
        or v.get("_lights", {}).get("emissionsIsOn")
    ]
    if not critical:
        return []

    elements = []
    elements.append(PageBreak())
    hdr_tbl = _make_section_header_table(
        styles,
        f"ACTION ITEMS  \u2502  {len(critical)} truck{'s' if len(critical) != 1 else ''} need attention",
        C_RED,
    )
    elements.append(hdr_tbl)
    elements.append(Spacer(1, 6))

    page_w = 7.1 * inch
    col_w = [0.60 * inch, 0.75 * inch, 2.50 * inch, 3.25 * inch]
    tbl_hdr = [
        Paragraph("<b>Truck</b>",    styles["CellBold"]),
        Paragraph("<b>Severity</b>", styles["CellBold"]),
        Paragraph("<b>Top Fault</b>", styles["CellBold"]),
        Paragraph("<b>Recommended Action</b>", styles["CellBold"]),
    ]
    tbl_data = [tbl_hdr]

    for v in critical:
        lights = v.get("_lights", {})
        dtcs = v.get("_dtcs", [])
        sev_label, _ = _severity_label(lights)
        top_fault = _spn_display(dtcs[0]) if dtcs else "Unknown"

        if sev_label == "STOP":
            action = "Shut down immediately. Do not operate until inspected."
            sev_hex = "#e94560"
        elif sev_label == "PROTECT":
            action = "Reduce load and speed. Schedule urgent service."
            sev_hex = "#f59e0b"
        else:
            action = "Schedule emissions inspection within 48 hours."
            sev_hex = "#eab308"

        tbl_data.append([
            Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"]),
            Paragraph(f'<font color="{sev_hex}"><b>{sev_label}</b></font>',
                      styles["CellBold"]),
            Paragraph(top_fault, styles["CellText"]),
            Paragraph(action, styles["CellText"]),
        ])

    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    n = len(tbl_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_RED),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_RED),
    ]
    for r in range(1, n):
        if r % 2 == 0:
            cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#fef2f2")))
    tbl.setStyle(TableStyle(cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 10))
    return elements


def _drive_idle_bar(drv_pct: float, width: float = 70, height: float = 10) -> Drawing:
    """Return a small Drawing with a green/amber stacked bar for drive/idle split."""
    d = Drawing(width, height)
    drv_w = width * drv_pct / 100
    idle_w = width - drv_w
    if drv_w > 0:
        d.add(Rect(0, 0, drv_w, height, fillColor=C_GREEN, strokeColor=None))
    if idle_w > 0:
        d.add(Rect(drv_w, 0, idle_w, height, fillColor=C_IDLE, strokeColor=None))
    return d


# ── Per-Truck Fault Card ────────────────────────────────────────

def _build_truck_card(v: dict, styles, show_org: bool = False) -> list:
    """One truck card: structured info table + DTC table with per-row severity."""
    elements = []
    page_w = 7.1 * inch

    lights   = v.get("_lights", {})
    dtcs     = v.get("_dtcs", [])
    loc      = v.get("location", {})
    fuel     = v.get("fuel", {})
    fuel_pct = fuel.get("value")
    fc_time  = v.get("fault_codes", {}).get("time", "") or v.get("_fault_time", "")
    co_code = v.get("_org", "")

    sev_label, card_bg = _severity_label(lights)
    loc_str    = _short_location(loc)
    lights_str = _light_badges_text(lights)
    fuel_str   = f"{fuel_pct}%" if fuel_pct is not None else "\u2014"

    # === Truck header banner (colored by severity) ===
    co_tag = f"[{co_code}] " if show_org and co_code else ""
    truck_title = (
        f"  {co_tag}Truck #{v.get('name', '?')}  \u2502  "
        f"{_safe(v.get('year'))} {_safe(v.get('make'))} {_safe(v.get('model'))}  \u2502  "
        f"{sev_label}"
    )
    sev_banner_color = C_RED if sev_label in ("STOP", "PROTECT") else (
        C_ORANGE if sev_label == "EMISSIONS" else (
        C_YELLOW if sev_label == "WARNING" else C_GRAY))
    banner = Table(
        [[Paragraph(truck_title, styles["CompanyBanner"])]],
        colWidths=[page_w],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), sev_banner_color),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(banner)

    # === Structured info table ===
    # Color-coded fuel
    fuel_hex = "#22c55e"
    if fuel_pct is None:
        fuel_hex = "#94a3b8"
    elif fuel_pct <= 15:
        fuel_hex = "#e94560"
    elif fuel_pct <= 30:
        fuel_hex = "#f59e0b"
    fuel_display = f'<font color="{fuel_hex}"><b>{fuel_str}</b></font>'

    # Color-coded dashboard lights
    dash_hex = "#22c55e" if lights_str == "Clear" else "#e94560"
    dash_display = f'<font color="{dash_hex}"><b>{lights_str}</b></font>'

    scan_str = _fmt_time_with_age(fc_time) if fc_time else "\u2014"

    info_col_w = [1.30 * inch, 2.25 * inch, 1.30 * inch, 2.25 * inch]
    info_rows = [
        [
            Paragraph("<b>VIN</b>", styles["CellBold"]),
            Paragraph(f'<font face="Courier">{_safe(v.get("vin"), "N/A")}</font>',
                      styles["CellText"]),
            Paragraph("<b>Plate</b>", styles["CellBold"]),
            Paragraph(_safe(v.get("license_plate"), "N/A"), styles["CellText"]),
        ],
        [
            Paragraph("<b>Location</b>", styles["CellBold"]),
            Paragraph(loc_str, styles["CellText"]),
            Paragraph("<b>Fuel</b>", styles["CellBold"]),
            Paragraph(fuel_display, styles["CellBold"]),
        ],
        [
            Paragraph("<b>Dash Lights</b>", styles["CellBold"]),
            Paragraph(dash_display, styles["CellBold"]),
            Paragraph("<b>Last Scan</b>", styles["CellBold"]),
            Paragraph(scan_str, styles["CellText"]),
        ],
    ]
    info_tbl = Table(info_rows, colWidths=info_col_w)
    info_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), C_LIGHT_BG),
        ("BACKGROUND",    (2, 0), (2, -1), C_LIGHT_BG),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.6, C_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(info_tbl)

    # === DTC table ===
    if dtcs:
        elements.append(Spacer(1, 4))
        col_widths = [
            0.30 * inch,   # #
            0.55 * inch,   # SPN
            0.40 * inch,   # FMI
            2.40 * inch,   # Issue
            1.90 * inch,   # Severity
            0.45 * inch,   # Cnt
            1.10 * inch,   # Source
        ]
        # Sum = 7.10" — full page width

        hdr = [
            Paragraph("<b>#</b>",        styles["CellBold"]),
            Paragraph("<b>SPN</b>",      styles["CellBold"]),
            Paragraph("<b>FMI</b>",      styles["CellBold"]),
            Paragraph("<b>Issue</b>",    styles["CellBold"]),
            Paragraph("<b>Severity</b>", styles["CellBold"]),
            Paragraph("<b>Cnt</b>",      styles["CellBold"]),
            Paragraph("<b>Source</b>",   styles["CellBold"]),
        ]
        table_data = [hdr]
        row_severity_info = []

        for i, dtc in enumerate(dtcs, 1):
            spn_id   = _safe(dtc.get("spnId"), "?")
            fmi_id   = _safe(dtc.get("fmiId"), "?")
            issue    = _spn_display(dtc)
            severity = _fmi_display(dtc)
            occ      = dtc.get("occurrenceCount")
            source   = _safe(dtc.get("sourceAddressName"), "\u2014")

            row_bg, dot_hex, dot_sym = _dtc_severity_info(
                dtc.get("fmiDescription", "")
            )
            row_severity_info.append((row_bg, dot_hex, dot_sym))

            occ_display = _occ_trend(occ)

            num_cell = Paragraph(
                f'<font color="{dot_hex}">{dot_sym}</font> {i}',
                styles["CellText"],
            )
            spn_cell = Paragraph(f"<b>{spn_id}</b>", styles["CellBold"])
            fmi_cell = Paragraph(f"<b>{fmi_id}</b>", styles["CellBold"])

            table_data.append([
                num_cell,
                spn_cell,
                fmi_cell,
                Paragraph(issue,    styles["CellText"]),
                Paragraph(severity, styles["CellText"]),
                Paragraph(occ_display, styles["CellText"]),
                Paragraph(source,   styles["CellText"]),
            ])

        dtc_table = Table(table_data, colWidths=col_widths, repeatRows=1)

        cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
            ("ALIGN",         (0, 0), (0, -1), "CENTER"),
            ("ALIGN",         (1, 0), (2, -1), "CENTER"),
            ("ALIGN",         (5, 0), (5, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
        ]

        for idx, (bg_color, _, _) in enumerate(row_severity_info):
            row_num = idx + 1
            cmds.append(("BACKGROUND", (0, row_num), (-1, row_num), bg_color))

        dtc_table.setStyle(TableStyle(cmds))
        elements.append(dtc_table)

    return [KeepTogether(elements)]


# ── Fleet Health Overview (for enhanced faults PDF) ──────────────

def _add_fleet_health_overview(story, styles, all_vehicles, stats):
    """Render fleet health dashboard: health score, severity, fuel tiers."""
    page_w = 7.1 * inch
    health_pct = round(stats["clean"] / stats["total"] * 100) if stats["total"] else 0
    health_color = C_GREEN if health_pct >= 80 else (C_ORANGE if health_pct >= 60 else C_RED)

    _add_section_header(story, styles, "FLEET HEALTH OVERVIEW", C_ACCENT)

    # Row 1: Health score + overall counts
    row1 = [[
        _mini_stat(styles, f"{health_pct}%", "Fleet Health", health_color),
        _mini_stat(styles, str(stats["total"]), "Total Trucks", C_ACCENT),
        _mini_stat(styles, str(stats["faulted"]), "With Faults", C_RED),
        _mini_stat(styles, str(stats["clean"]), "Clean", C_GREEN),
        _mini_stat(styles, str(stats["total_dtcs"]), "Total DTCs", C_ORANGE),
    ]]
    t1 = Table(row1, colWidths=[page_w / 5] * 5, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    story.append(t1)
    story.append(Spacer(1, 4))

    # Row 2: Severity breakdown
    row2 = [[
        _mini_stat(styles, str(stats["stop"]), "STOP", C_RED),
        _mini_stat(styles, str(stats["protect"]), "PROTECT", C_RED),
        _mini_stat(styles, str(stats["emissions"]), "EMISSIONS", C_ORANGE),
        _mini_stat(styles, str(stats["warning"]), "WARNING", C_YELLOW),
        _mini_stat(styles, str(stats["minor"]), "MINOR", C_GREEN),
    ]]
    t2 = Table(row2, colWidths=[page_w / 5] * 5, rowHeights=[48])
    t2.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    story.append(t2)
    story.append(Spacer(1, 4))

    # Row 3: Fuel tier breakdown
    fuel_crit = fuel_low = fuel_good = fuel_unknown = 0
    for v in all_vehicles:
        pct = v.get("fuel", {}).get("value")
        if pct is None:
            fuel_unknown += 1
        elif pct <= 15:
            fuel_crit += 1
        elif pct <= 30:
            fuel_low += 1
        else:
            fuel_good += 1

    row3 = [[
        _mini_stat(styles, str(fuel_crit), "Critical \u226415%", C_RED),
        _mini_stat(styles, str(fuel_low), "Low 16\u201330%", C_ORANGE),
        _mini_stat(styles, str(fuel_good), "Good >30%", C_GREEN),
        _mini_stat(styles, str(fuel_unknown), "No Data", C_GRAY),
    ]]
    t3 = Table(row3, colWidths=[page_w / 4] * 4, rowHeights=[52])
    t3.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    story.append(t3)
    story.append(Spacer(1, 10))


# ── Fleet Status Grid (all trucks at a glance) ──────────────────

def _add_fleet_status_grid(story, styles, all_vehicles, show_org=False):
    """Compact color-coded table of ALL trucks with status indicators."""
    page_w = 7.1 * inch

    def _status_info(v):
        fc = v.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = j1939.get("diagnosticTroubleCodes", [])
        cel = j1939.get("checkEngineLights", {})
        n = len(dtcs)
        if not dtcs:
            return "\u2713 OK", C_GREEN, 10, n
        if cel.get("stopIsOn"):
            return "\u25cf STOP", C_RED, 0, n
        if cel.get("protectIsOn"):
            return "\u25cf PROT", C_RED, 1, n
        if cel.get("emissionsIsOn"):
            return "\u25cf EMIS", C_ORANGE, 2, n
        if cel.get("warningIsOn"):
            return "\u25cf WARN", C_YELLOW, 3, n
        return "\u25cf MINOR", C_GRAY, 5, n

    rows_info = []
    for v in all_vehicles:
        st_text, st_color, rank, n_dtcs = _status_info(v)
        fuel_pct = v.get("fuel", {}).get("value")
        rows_info.append((v, st_text, st_color, rank, n_dtcs, fuel_pct))
    rows_info.sort(key=lambda x: (x[3], x[0].get("name", "")))

    count_text = (
        f"ALL TRUCKS STATUS  \u2502  {len(rows_info)} truck"
        f"{'s' if len(rows_info) != 1 else ''}"
    )
    _add_section_header(story, styles, count_text, C_ACCENT)

    if show_org:
        col_w = [0.25 * inch, 0.55 * inch, 0.75 * inch, 0.65 * inch,
                 0.50 * inch, 0.40 * inch, 1.60 * inch, 2.40 * inch]
        hdr = [
            Paragraph("<b>#</b>",       styles["CellBold"]),
            Paragraph("<b>Truck</b>",   styles["CellBold"]),
            Paragraph("<b>Company</b>", styles["CellBold"]),
            Paragraph("<b>Status</b>",  styles["CellBold"]),
            Paragraph("<b>Fuel</b>",    styles["CellBold"]),
            Paragraph("<b>DTCs</b>",    styles["CellBold"]),
            Paragraph("<b>Location</b>", styles["CellBold"]),
            Paragraph("<b>Vehicle</b>", styles["CellBold"]),
        ]
        status_col, fuel_col, dtc_col = 3, 4, 5
    else:
        col_w = [0.25 * inch, 0.60 * inch, 0.70 * inch, 0.50 * inch,
                 0.45 * inch, 2.00 * inch, 2.60 * inch]
        hdr = [
            Paragraph("<b>#</b>",       styles["CellBold"]),
            Paragraph("<b>Truck</b>",   styles["CellBold"]),
            Paragraph("<b>Status</b>",  styles["CellBold"]),
            Paragraph("<b>Fuel</b>",    styles["CellBold"]),
            Paragraph("<b>DTCs</b>",    styles["CellBold"]),
            Paragraph("<b>Location</b>", styles["CellBold"]),
            Paragraph("<b>Vehicle</b>", styles["CellBold"]),
        ]
        status_col, fuel_col, dtc_col = 2, 3, 4

    table_data = [hdr]
    row_bgs = []

    for i, (v, st_text, st_color, rank, n_dtcs, fuel_pct) in enumerate(
        rows_info, 1,
    ):
        fuel_str = f"{fuel_pct}%" if fuel_pct is not None else "\u2014"
        loc_str = _short_location(v.get("location", {}))
        veh_info = (
            f"{_safe(v.get('year'))} {_safe(v.get('make'))} "
            f"{_safe(v.get('model'))}"
        )
        st_hex = st_color.hexval()
        status_cell = Paragraph(
            f'<font color="{st_hex}"><b>{st_text}</b></font>',
            styles["CellBold"],
        )

        fuel_hex = "#22c55e"
        if fuel_pct is None:
            fuel_hex = "#94a3b8"
        elif fuel_pct <= 15:
            fuel_hex = "#e94560"
        elif fuel_pct <= 30:
            fuel_hex = "#f59e0b"
        fuel_cell = Paragraph(
            f'<font color="{fuel_hex}"><b>{fuel_str}</b></font>',
            styles["CellBold"],
        )

        dtc_str = str(n_dtcs) if n_dtcs else "\u2014"
        dtc_hex = "#e94560" if n_dtcs else "#94a3b8"
        dtc_cell = Paragraph(
            f'<font color="{dtc_hex}"><b>{dtc_str}</b></font>',
            styles["CellBold"],
        )

        row = [
            Paragraph(str(i), styles["CellText"]),
            Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"]),
        ]
        if show_org:
            row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
        row.extend([
            status_cell, fuel_cell, dtc_cell,
            Paragraph(loc_str, styles["CellText"]),
            Paragraph(veh_info, styles["CellText"]),
        ])
        table_data.append(row)

        if rank <= 2:
            row_bgs.append(ROW_MOST_SEVERE)
        elif rank == 3:
            row_bgs.append(ROW_LEAST)
        elif rank == 5:
            row_bgs.append(C_MINOR_BG)
        else:
            row_bgs.append(None)

    grid = Table(table_data, colWidths=col_w, repeatRows=1)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (status_col, 0), (status_col, -1), "CENTER"),
        ("ALIGN",         (fuel_col, 0), (fuel_col, -1), "CENTER"),
        ("ALIGN",         (dtc_col, 0), (dtc_col, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    for idx, bg in enumerate(row_bgs):
        row_num = idx + 1
        if bg is not None:
            cmds.append(("BACKGROUND", (0, row_num), (-1, row_num), bg))
        elif idx % 2 == 1:
            cmds.append(("BACKGROUND", (0, row_num), (-1, row_num), C_LIGHT_BG))
    grid.setStyle(TableStyle(cmds))
    story.append(grid)
    story.append(Spacer(1, 10))


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate fleet efficiency report (merged engine hours + driver efficiency)
# ══════════════════════════════════════════════════════════════════

# Extra color for engine hours bars
C_BLUE = colors.HexColor("#3b82f6")
C_IDLE = colors.HexColor("#f59e0b")
C_EFF_GREEN = colors.HexColor("#22c55e")
C_EFF_AMBER = colors.HexColor("#f59e0b")
C_EFF_BLUE  = colors.HexColor("#3b82f6")


def generate_fleet_efficiency_pdf(
    vehicles: list[dict],
    days: int = 7,
    company_filter: str | None = None,
) -> io.BytesIO:
    """Generate a merged efficiency PDF (engine hours + driver metrics).

    Landscape orientation to fit all columns.
    Each vehicle dict must contain engine-hours fields (always) and
    optional driver enrichment fields (_driver_name, _fuel_gal, etc.)
    which are None when no driver is assigned.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.4 * inch, rightMargin=0.4 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")
    page_w = 10.2 * inch

    # ── Header ───────────────────────────────────────────────────
    subtitle = f"Efficiency Report — Past {days} Days"
    if company_filter:
        subtitle = (
            f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — "
            f"Efficiency ({days} Days)"
        )
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Summary dashboard ────────────────────────────────────────
    total_eng_s = sum(v.get("_engine_s", v["_engine_hours"] * 3600) for v in vehicles)
    total_drv_s = sum(v.get("_driving_s", v["_driving_hours"] * 3600) for v in vehicles)
    total_idle_s = sum(v.get("_idle_s", v["_idle_hours"] * 3600) for v in vehicles)
    total_eng = total_eng_s / 3600
    total_drv = total_drv_s / 3600
    total_idle = total_idle_s / 3600
    total_miles = sum(v.get("_miles", 0) for v in vehicles)
    avg_drv_pct = (total_drv_s / total_eng_s * 100) if total_eng_s > 0 else 0

    with_driver = [v for v in vehicles if v.get("_driver_name")]
    total_fuel = sum(v["_fuel_gal"] for v in with_driver if v.get("_fuel_gal"))
    fuel_miles = sum(v.get("_miles", 0) for v in with_driver)
    fleet_mpg = fuel_miles / total_fuel if total_fuel > 0 else 0

    ncols = 8
    row1 = [[
        _mini_stat(styles, str(len(vehicles)), "Trucks", C_ACCENT),
        _mini_stat(styles, str(len(with_driver)), "Drivers", C_EFF_BLUE),
        _mini_stat(styles, f"{total_eng:,.1f}h", "Engine Time", C_BLUE),
        _mini_stat(styles, f"{total_miles:,}mi", "Miles", C_ACCENT),
        _mini_stat(styles, f"{total_drv:,.1f}h", "Driving", C_GREEN),
        _mini_stat(styles, f"{total_idle:,.1f}h", "Idle", C_IDLE),
        _mini_stat(styles, f"{avg_drv_pct:.0f}%", "Avg Driving", C_GREEN),
        _mini_stat(styles, f"{fleet_mpg:.1f}", "Fleet MPG", C_EFF_GREEN),
    ]]
    t1 = Table(row1, colWidths=[page_w / ncols] * ncols, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)
    story.append(Spacer(1, 14))

    # ── Per-company breakdown (if multi-company) ────────────────
    companies_seen: list[str] = []
    for v in vehicles:
        o = v.get("_org", "")
        if o and o not in companies_seen:
            companies_seen.append(o)

    if len(companies_seen) > 1 and not company_filter:
        col_widths = [2.4 * inch, 0.7 * inch, 0.7 * inch, 0.9 * inch,
                      0.9 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch,
                      0.9 * inch]
        hdr = [
            Paragraph("<b>Company</b>",    styles["OrgTableHeader"]),
            Paragraph("<b>Trucks</b>",     styles["OrgTableHeader"]),
            Paragraph("<b>Drivers</b>",    styles["OrgTableHeader"]),
            Paragraph("<b>Engine h</b>",   styles["OrgTableHeader"]),
            Paragraph("<b>Drive h</b>",    styles["OrgTableHeader"]),
            Paragraph("<b>Idle h</b>",     styles["OrgTableHeader"]),
            Paragraph("<b>Driving %</b>",  styles["OrgTableHeader"]),
            Paragraph("<b>MPG</b>",        styles["OrgTableHeader"]),
            Paragraph("<b>Idle %</b>",     styles["OrgTableHeader"]),
        ]
        co_table_data = [hdr]
        for oc in companies_seen:
            ov = [v for v in vehicles if v.get("_org") == oc]
            o_eng_s = sum(v.get("_engine_s", v["_engine_hours"] * 3600) for v in ov)
            o_drv_s = sum(v.get("_driving_s", v["_driving_hours"] * 3600) for v in ov)
            o_idle_s = o_eng_s - o_drv_s
            o_eng = o_eng_s / 3600
            o_drv = o_drv_s / 3600
            o_idle = o_idle_s / 3600
            o_pct = (o_drv_s / o_eng_s * 100) if o_eng_s > 0 else 0
            o_idle_pct = 100 - o_pct if o_eng_s > 0 else 0
            o_wd = [v for v in ov if v.get("_driver_name")]
            o_fuel = sum(v["_fuel_gal"] for v in o_wd if v.get("_fuel_gal"))
            o_fmi = sum(v.get("_miles", 0) for v in o_wd)
            o_mpg = o_fmi / o_fuel if o_fuel > 0 else 0
            co_name = COMPANY_DISPLAY.get(oc, oc)
            co_table_data.append([
                Paragraph(f"{co_name} ({oc})", styles["CompanyTableCell"]),
                Paragraph(str(len(ov)),          styles["CompanyTableCell"]),
                Paragraph(str(len(o_wd)),        styles["CompanyTableCell"]),
                Paragraph(f"{o_eng:,.1f}",       styles["CompanyTableCell"]),
                Paragraph(f"{o_drv:,.1f}",       styles["CompanyTableCell"]),
                Paragraph(f"{o_idle:,.1f}",      styles["CompanyTableCell"]),
                Paragraph(f"{o_pct:.0f}%",       styles["CompanyTableCell"]),
                Paragraph(f"{o_mpg:.1f}",        styles["CompanyTableCell"]),
                Paragraph(f"{o_idle_pct:.0f}%",  styles["CompanyTableCell"]),
            ])
        ot = Table(co_table_data, colWidths=col_widths)
        ot.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ]))
        story.append(ot)
        story.append(Spacer(1, 14))

    # ── Per-truck table ──────────────────────────────────────────
    _add_section_header(
        story, styles,
        f"EFFICIENCY BREAKDOWN  \u2502  {len(vehicles)} trucks",
        C_EFF_BLUE,
    )

    show_org = len(companies_seen) > 1 and not company_filter
    if show_org:
        col_widths = [
            0.55 * inch,   # Truck
            0.45 * inch,   # Co.
            0.85 * inch,   # Driver
            0.55 * inch,   # Engine h
            0.50 * inch,   # Miles
            0.55 * inch,   # Drive h
            0.50 * inch,   # Idle h
            0.50 * inch,   # Drv %
            0.50 * inch,   # Idle %
            0.45 * inch,   # Fuel
            0.45 * inch,   # MPG
            0.48 * inch,   # Eco %
            0.50 * inch,   # OvrSpd
            0.52 * inch,   # Brakes
        ]
        hdr = [
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Co.</b>",      styles["CellBold"]),
            Paragraph("<b>Driver</b>",   styles["CellBold"]),
            Paragraph("<b>Eng h</b>",    styles["CellBold"]),
            Paragraph("<b>Miles</b>",    styles["CellBold"]),
            Paragraph("<b>Drv h</b>",    styles["CellBold"]),
            Paragraph("<b>Idle h</b>",   styles["CellBold"]),
            Paragraph("<b>Drv %</b>",    styles["CellBold"]),
            Paragraph("<b>Idle %</b>",   styles["CellBold"]),
            Paragraph("<b>Fuel</b>",     styles["CellBold"]),
            Paragraph("<b>MPG</b>",      styles["CellBold"]),
            Paragraph("<b>Eco %</b>",    styles["CellBold"]),
            Paragraph("<b>OvrSpd</b>",   styles["CellBold"]),
            Paragraph("<b>Brakes</b>",   styles["CellBold"]),
        ]
    else:
        col_widths = [
            0.60 * inch,   # Truck
            0.95 * inch,   # Driver
            0.60 * inch,   # Engine h
            0.55 * inch,   # Miles
            0.60 * inch,   # Drive h
            0.55 * inch,   # Idle h
            0.55 * inch,   # Drv %
            0.55 * inch,   # Idle %
            0.50 * inch,   # Fuel
            0.50 * inch,   # MPG
            0.50 * inch,   # Eco %
            0.55 * inch,   # OvrSpd
            0.55 * inch,   # Brakes
        ]
        hdr = [
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Driver</b>",   styles["CellBold"]),
            Paragraph("<b>Eng h</b>",    styles["CellBold"]),
            Paragraph("<b>Miles</b>",    styles["CellBold"]),
            Paragraph("<b>Drv h</b>",    styles["CellBold"]),
            Paragraph("<b>Idle h</b>",   styles["CellBold"]),
            Paragraph("<b>Drv %</b>",    styles["CellBold"]),
            Paragraph("<b>Idle %</b>",   styles["CellBold"]),
            Paragraph("<b>Fuel</b>",     styles["CellBold"]),
            Paragraph("<b>MPG</b>",      styles["CellBold"]),
            Paragraph("<b>Eco %</b>",    styles["CellBold"]),
            Paragraph("<b>OvrSpd</b>",   styles["CellBold"]),
            Paragraph("<b>Brakes</b>",   styles["CellBold"]),
        ]

    table_data = [hdr]
    for v in vehicles:
        drv_pct = v["_driving_pct"]
        idle_pct = v["_idle_pct"]
        miles = v.get("_miles", 0)
        driver = v.get("_driver_name")
        fuel = v.get("_fuel_gal")
        mpg_val = v.get("_mpg")
        eco_val = v.get("_green_pct")
        ovr_val = v.get("_overspeed_min")
        antic = v.get("_antic_brakes")
        total_brk = v.get("_total_brakes")

        # Color coding for driver metrics
        if mpg_val is not None:
            mpg_clr = "#22c55e" if mpg_val >= 6 else ("#f59e0b" if mpg_val >= 4 else "#e94560")
            mpg_txt = f'<font color="{mpg_clr}"><b>{mpg_val}</b></font>'
        else:
            mpg_txt = "—"
        if eco_val is not None:
            eco_clr = "#22c55e" if eco_val >= 60 else ("#f59e0b" if eco_val >= 30 else "#e94560")
            eco_txt = f'<font color="{eco_clr}"><b>{eco_val}%</b></font>'
        else:
            eco_txt = "—"
        if ovr_val is not None:
            ovr_clr = "#22c55e" if ovr_val < 1 else ("#f59e0b" if ovr_val < 5 else "#e94560")
            ovr_txt = f'<font color="{ovr_clr}">{ovr_val}m</font>'
        else:
            ovr_txt = "—"
        brk_txt = f"{antic}/{total_brk}" if antic is not None else "—"

        row = [Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"])]
        if show_org:
            row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
        row.extend([
            Paragraph(driver or "—", styles["CellText"]),
            Paragraph(f"{v['_engine_hours']}", styles["CellText"]),
            Paragraph(f"{miles:,}", styles["CellText"]),
            Paragraph(f"{v['_driving_hours']}", styles["CellText"]),
            Paragraph(f"{v['_idle_hours']}", styles["CellText"]),
            Paragraph(f"{drv_pct}%", styles["CellText"]),
            Paragraph(f"{idle_pct}%", styles["CellText"]),
            Paragraph(f"{fuel}" if fuel is not None else "—", styles["CellText"]),
            Paragraph(mpg_txt, styles["CellText"]),
            Paragraph(eco_txt, styles["CellText"]),
            Paragraph(ovr_txt, styles["CellText"]),
            Paragraph(brk_txt, styles["CellText"]),
        ])
        table_data.append(row)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    num_rows = len(table_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    for i in range(1, num_rows):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG))
    t.setStyle(TableStyle(cmds))
    story.append(t)

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate fuel level report
# ══════════════════════════════════════════════════════════════════

def generate_fuel_report_pdf(
    all_vehicles: list[dict],
    company_filter: str | None = None,
) -> io.BytesIO:
    """Generate a professional fuel level PDF report for the entire fleet.

    Each vehicle dict must have ``fuel`` (with ``value`` and ``time``)
    and ``_org`` keys from MultiCompanyClient.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")
    page_w = 7.1 * inch

    # ── Header ───────────────────────────────────────────────────
    subtitle = "Fleet Fuel Level Report"
    if company_filter:
        subtitle = f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — Fuel Report"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Classify vehicles ────────────────────────────────────────
    critical_list = []   # <= 15%
    low_list = []        # 16-30%
    good_list = []       # > 30%
    no_data_list = []    # None

    for v in all_vehicles:
        pct = v.get("fuel", {}).get("value")
        if pct is None:
            no_data_list.append(v)
        elif pct <= 15:
            critical_list.append(v)
        elif pct <= 30:
            low_list.append(v)
        else:
            good_list.append(v)

    # Sort each tier by fuel ascending
    critical_list.sort(key=lambda x: x.get("fuel", {}).get("value", 0))
    low_list.sort(key=lambda x: x.get("fuel", {}).get("value", 0))
    good_list.sort(key=lambda x: x.get("fuel", {}).get("value", 999))

    total = len(all_vehicles)
    avg_fuel = 0.0
    known = [v for v in all_vehicles if v.get("fuel", {}).get("value") is not None]
    if known:
        avg_fuel = sum(v["fuel"]["value"] for v in known) / len(known)

    # DEF stats
    def_known = [v for v in all_vehicles if v.get("def_level", {}).get("value") is not None]
    avg_def = sum(v["def_level"]["value"] for v in def_known) / len(def_known) if def_known else 0
    low_def_count = sum(1 for v in def_known if v["def_level"]["value"] < 15)

    # ── Summary dashboard ────────────────────────────────────────
    _add_section_header(story, styles, "FUEL LEVEL OVERVIEW", C_ACCENT)

    row1 = [[
        _mini_stat(styles, str(total), "Total Trucks", C_ACCENT),
        _mini_stat(styles, f"{avg_fuel:.0f}%", "Avg Fuel", C_BLUE),
        _mini_stat(styles, str(len(critical_list)), "Critical \u226415%", C_RED),
        _mini_stat(styles, str(len(low_list)), "Low 16\u201330%", C_ORANGE),
        _mini_stat(styles, f"{avg_def:.0f}%", "Avg DEF", C_HEALTH_BLUE),
        _mini_stat(styles, str(low_def_count), "Low DEF", C_ORANGE),
    ]]
    t1 = Table(row1, colWidths=[page_w / 5] * 5, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    story.append(t1)
    story.append(Spacer(1, 10))

    # ── Per-company breakdown (multi-company only) ───────────────────────
    companies_seen: list[str] = []
    for v in all_vehicles:
        o = v.get("_org", "")
        if o and o not in companies_seen:
            companies_seen.append(o)

    if len(companies_seen) > 1 and not company_filter:
        col_widths = [2.2 * inch, 0.7 * inch, 0.7 * inch, 0.7 * inch,
                      0.65 * inch, 0.65 * inch, 0.7 * inch, 0.6 * inch]
        hdr = [
            Paragraph("<b>Company</b>",   styles["OrgTableHeader"]),
            Paragraph("<b>Trucks</b>",    styles["OrgTableHeader"]),
            Paragraph("<b>Avg Fuel</b>",  styles["OrgTableHeader"]),
            Paragraph("<b>Critical</b>",  styles["OrgTableHeader"]),
            Paragraph("<b>Low</b>",       styles["OrgTableHeader"]),
            Paragraph("<b>Good</b>",      styles["OrgTableHeader"]),
            Paragraph("<b>Avg DEF</b>",   styles["OrgTableHeader"]),
            Paragraph("<b>N/A</b>",       styles["OrgTableHeader"]),
        ]
        co_rows = [hdr]

        for oc in sorted(companies_seen):
            ov = [v for v in all_vehicles if v.get("_org") == oc]
            o_known = [v for v in ov if v.get("fuel", {}).get("value") is not None]
            o_avg = sum(v["fuel"]["value"] for v in o_known) / len(o_known) if o_known else 0
            o_crit = sum(1 for v in ov if (v.get("fuel", {}).get("value") or 999) <= 15)
            o_low = sum(1 for v in ov if 15 < (v.get("fuel", {}).get("value") or 999) <= 30)
            o_good = sum(1 for v in ov if (v.get("fuel", {}).get("value") or 0) > 30)
            o_na = sum(1 for v in ov if v.get("fuel", {}).get("value") is None)
            o_def_known = [v for v in ov if v.get("def_level", {}).get("value") is not None]
            o_avg_def = sum(v["def_level"]["value"] for v in o_def_known) / len(o_def_known) if o_def_known else 0
            co_name = COMPANY_DISPLAY.get(oc, oc)
            co_rows.append([
                Paragraph(f"{co_name} ({oc})", styles["CompanyTableCell"]),
                Paragraph(str(len(ov)),         styles["CompanyTableCell"]),
                Paragraph(f"{o_avg:.0f}%",      styles["CompanyTableCell"]),
                Paragraph(str(o_crit),          styles["CompanyTableCell"]),
                Paragraph(str(o_low),           styles["CompanyTableCell"]),
                Paragraph(str(o_good),          styles["CompanyTableCell"]),
                Paragraph(f"{o_avg_def:.0f}%",  styles["CompanyTableCell"]),
                Paragraph(str(o_na),            styles["CompanyTableCell"]),
            ])

        ot = Table(co_rows, colWidths=col_widths)
        ot.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ]))
        story.append(ot)
        story.append(Spacer(1, 12))

    # ── Fuel tier sections with per-truck rows ───────────────────
    show_org = len(companies_seen) > 1 and not company_filter
    tiers = [
        ("CRITICAL FUEL  \u2264 15%", C_RED, critical_list),
        ("LOW FUEL  16\u201330%", C_ORANGE, low_list),
        ("GOOD FUEL  > 30%", C_GREEN, good_list),
    ]

    if show_org:
        col_w = [0.25 * inch, 0.55 * inch, 0.70 * inch, 0.50 * inch,
                 0.50 * inch, 0.55 * inch, 1.50 * inch, 2.55 * inch]
    else:
        col_w = [0.25 * inch, 0.60 * inch, 0.55 * inch, 0.50 * inch,
                 0.55 * inch, 1.80 * inch, 2.85 * inch]

    for tier_label, tier_color, tier_vehicles in tiers:
        if not tier_vehicles:
            continue
        count_text = (
            f"{tier_label}  \u2502  {len(tier_vehicles)} truck"
            f"{'s' if len(tier_vehicles) != 1 else ''}"
        )
        _add_section_header(story, styles, count_text, tier_color)

        if show_org:
            hdr = [
                Paragraph("<b>#</b>",       styles["CellBold"]),
                Paragraph("<b>Truck</b>",   styles["CellBold"]),
                Paragraph("<b>Company</b>", styles["CellBold"]),
                Paragraph("<b>Fuel</b>",    styles["CellBold"]),
                Paragraph("<b>DEF</b>",     styles["CellBold"]),
                Paragraph("<b>Updated</b>", styles["CellBold"]),
                Paragraph("<b>Location</b>", styles["CellBold"]),
                Paragraph("<b>Vehicle</b>", styles["CellBold"]),
            ]
            fuel_col_idx = 3
            def_col_idx = 4
        else:
            hdr = [
                Paragraph("<b>#</b>",       styles["CellBold"]),
                Paragraph("<b>Truck</b>",   styles["CellBold"]),
                Paragraph("<b>Fuel</b>",    styles["CellBold"]),
                Paragraph("<b>DEF</b>",     styles["CellBold"]),
                Paragraph("<b>Updated</b>", styles["CellBold"]),
                Paragraph("<b>Location</b>", styles["CellBold"]),
                Paragraph("<b>Vehicle</b>", styles["CellBold"]),
            ]
            fuel_col_idx = 2
            def_col_idx = 3

        tbl_data = [hdr]
        for i, v in enumerate(tier_vehicles, 1):
            fuel_v = v.get("fuel", {})
            pct = fuel_v.get("value")
            fuel_str = f"{pct}%" if pct is not None else "\u2014"
            fuel_time_str = _fmt_time(fuel_v.get("time", ""))
            loc_str = _short_location(v.get("location", {}))
            veh_info = (
                f"{_safe(v.get('year'))} {_safe(v.get('make'))} "
                f"{_safe(v.get('model'))}"
            )
            # Color the fuel % cell
            fuel_hex = "#22c55e"
            if pct is None:
                fuel_hex = "#94a3b8"
            elif pct <= 15:
                fuel_hex = "#e94560"
            elif pct <= 30:
                fuel_hex = "#f59e0b"

            # DEF level
            def_v = v.get("def_level", {})
            def_pct = def_v.get("value")
            def_str = f"{def_pct:.0f}%" if def_pct is not None else "\u2014"
            def_hex = "#94a3b8"
            if def_pct is not None:
                def_hex = "#e94560" if def_pct < 10 else ("#f59e0b" if def_pct < 20 else "#22c55e")

            row = [
                Paragraph(str(i), styles["CellText"]),
                Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"]),
            ]
            if show_org:
                row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
            row.extend([
                Paragraph(f'<font color="{fuel_hex}"><b>{fuel_str}</b></font>',
                          styles["CellBold"]),
                Paragraph(f'<font color="{def_hex}"><b>{def_str}</b></font>',
                          styles["CellBold"]),
                Paragraph(fuel_time_str, styles["CellText"]),
                Paragraph(loc_str, styles["CellText"]),
                Paragraph(veh_info, styles["CellText"]),
            ])
            tbl_data.append(row)

        tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
        num_rows = len(tbl_data)
        t_cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
            ("ALIGN",         (0, 0), (0, -1), "CENTER"),
            ("ALIGN",         (fuel_col_idx, 0), (fuel_col_idx, -1), "CENTER"),
            ("ALIGN",         (def_col_idx, 0), (def_col_idx, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
            ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
        ]
        for r in range(1, num_rows):
            if r % 2 == 0:
                t_cmds.append(("BACKGROUND", (0, r), (-1, r), C_LIGHT_BG))
        tbl.setStyle(TableStyle(t_cmds))
        story.append(tbl)
        story.append(Spacer(1, 8))

    # ── No data tier (compact) ───────────────────────────────────
    if no_data_list:
        count_text = (
            f"NO FUEL DATA  \u2502  {len(no_data_list)} truck"
            f"{'s' if len(no_data_list) != 1 else ''}"
        )
        _add_section_header(story, styles, count_text, C_GRAY)
        names = ", ".join(f"#{v.get('name', '?')}" for v in no_data_list)
        story.append(Paragraph(names, styles["TruckMeta"]))
        story.append(Spacer(1, 8))

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate vehicle health report
# ══════════════════════════════════════════════════════════════════

C_HEALTH_GOOD = colors.HexColor("#22c55e")
C_HEALTH_WARN = colors.HexColor("#f59e0b")
C_HEALTH_CRIT = colors.HexColor("#e94560")
C_HEALTH_BLUE = colors.HexColor("#3b82f6")


def _latest_reading(health: dict) -> tuple[str, str]:
    """Return (relative_time_str, color_hex) for the most recent sensor reading.

    health dict has keys like battery_time, oil_time, coolant_time, etc.
    """
    time_keys = [k for k in health if k.endswith("_time")]
    if not time_keys:
        return ("\u2014", "#94a3b8")

    latest = ""
    for k in time_keys:
        t = health[k]
        if t and t > latest:
            latest = t
    if not latest:
        return ("\u2014", "#94a3b8")

    try:
        dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            txt = f"{mins}m ago"
        elif mins < 1440:
            txt = f"{mins // 60}h ago"
        else:
            txt = f"{mins // 1440}d ago"

        if mins < 120:
            clr = "#22c55e"      # green — fresh
        elif mins < 1440:
            clr = "#f59e0b"      # amber — hours old
        else:
            clr = "#e94560"      # red — stale (>1 day)
        return (txt, clr)
    except (ValueError, TypeError):
        return ("\u2014", "#94a3b8")


def generate_vehicle_health_pdf(
    vehicles: list[dict],
    company_filter: str | None = None,
) -> io.BytesIO:
    """Generate a vehicle health diagnostics PDF report."""
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")
    page_w = 7.1 * inch

    # ── Header ───────────────────────────────────────────────────
    subtitle = "Vehicle Health Dashboard"
    if company_filter:
        subtitle = f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — Vehicle Health"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Summary dashboard ────────────────────────────────────────
    alert_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    crit_count = sum(1 for v in vehicles if v.get("_health_alerts"))

    low_batt = sum(1 for v in vehicles if "low_battery" in v.get("_health_alerts", []))
    low_oil = sum(1 for v in vehicles if "low_oil_pressure" in v.get("_health_alerts", []))
    low_def = sum(1 for v in vehicles if "low_def" in v.get("_health_alerts", []))
    unbuckled = sum(1 for v in vehicles if "seatbelt_unbuckled" in v.get("_health_alerts", []))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    eng_off = len(vehicles) - eng_on

    row1 = [[
        _mini_stat(styles, str(len(vehicles)), "Trucks", C_ACCENT),
        _mini_stat(styles, f"{eng_on}/{eng_off}", "On / Off", C_HEALTH_GOOD),
        _mini_stat(styles, str(crit_count), "With Alerts", C_HEALTH_CRIT),
        _mini_stat(styles, str(low_batt), "Low Battery", C_HEALTH_WARN),
        _mini_stat(styles, str(low_oil), "Low Oil", C_HEALTH_CRIT),
        _mini_stat(styles, str(unbuckled), "Unbuckled", C_ORANGE),
    ]]
    t1 = Table(row1, colWidths=[page_w / 6] * 6, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)
    story.append(Spacer(1, 14))

    # ── Per-company breakdown ────────────────────────────────────────
    companies_seen: list[str] = []
    for v in vehicles:
        o = v.get("_org", "")
        if o and o not in companies_seen:
            companies_seen.append(o)

    show_org = len(companies_seen) > 1 and not company_filter

    # ── Vehicle health table ─────────────────────────────────────
    _add_section_header(
        story, styles,
        f"VEHICLE HEALTH  \u2502  {len(vehicles)} trucks",
        C_HEALTH_BLUE,
    )

    if show_org:
        col_w = [0.55*inch, 0.50*inch, 0.55*inch, 0.65*inch, 0.60*inch,
                 0.65*inch, 0.55*inch, 0.55*inch, 0.55*inch,
                 0.55*inch, 0.90*inch]
        hdr = [
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Co.</b>",      styles["CellBold"]),
            Paragraph("<b>Engine</b>",   styles["CellBold"]),
            Paragraph("<b>Battery</b>",  styles["CellBold"]),
            Paragraph("<b>Oil psi</b>",  styles["CellBold"]),
            Paragraph("<b>Coolant</b>",  styles["CellBold"]),
            Paragraph("<b>DEF %</b>",    styles["CellBold"]),
            Paragraph("<b>Load %</b>",   styles["CellBold"]),
            Paragraph("<b>Seatbelt</b>", styles["CellBold"]),
            Paragraph("<b>RPM</b>",      styles["CellBold"]),
            Paragraph("<b>Last Read</b>", styles["CellBold"]),
        ]
    else:
        col_w = [0.60*inch, 0.55*inch, 0.70*inch, 0.65*inch, 0.65*inch,
                 0.65*inch, 0.60*inch, 0.60*inch, 0.60*inch, 0.95*inch]
        hdr = [
            Paragraph("<b>Truck</b>",    styles["CellBold"]),
            Paragraph("<b>Engine</b>",   styles["CellBold"]),
            Paragraph("<b>Battery</b>",  styles["CellBold"]),
            Paragraph("<b>Oil psi</b>",  styles["CellBold"]),
            Paragraph("<b>Coolant</b>",  styles["CellBold"]),
            Paragraph("<b>DEF %</b>",    styles["CellBold"]),
            Paragraph("<b>Load %</b>",   styles["CellBold"]),
            Paragraph("<b>Seatbelt</b>", styles["CellBold"]),
            Paragraph("<b>RPM</b>",      styles["CellBold"]),
            Paragraph("<b>Last Read</b>", styles["CellBold"]),
        ]

    table_data = [hdr]
    for v in vehicles:
        h = v.get("_health", {})
        alerts = v.get("_health_alerts", [])

        def _color_val(val, key):
            if val is None:
                return "\u2014"
            if key == "battery_v":
                clr = "#e94560" if val < 12.2 else ("#f59e0b" if val < 12.6 else "#22c55e")
                return f'<font color="{clr}"><b>{val:.1f}V</b></font>'
            if key == "oil_psi":
                clr = "#e94560" if val < 10 else ("#f59e0b" if val < 20 else "#22c55e")
                return f'<font color="{clr}"><b>{val:.0f}</b></font>'
            if key == "coolant_c":
                clr = "#e94560" if val > 105 else ("#f59e0b" if val > 95 else "#22c55e")
                return f'<font color="{clr}"><b>{val:.0f}\u00b0C</b></font>'
            if key == "def_pct":
                clr = "#e94560" if val < 10 else ("#f59e0b" if val < 20 else "#22c55e")
                return f'<font color="{clr}"><b>{val:.0f}%</b></font>'
            return str(val)

        batt_str = _color_val(h.get("battery_v"), "battery_v")
        oil_str = _color_val(h.get("oil_psi"), "oil_psi")
        cool_str = _color_val(h.get("coolant_c"), "coolant_c")
        def_str = _color_val(h.get("def_pct"), "def_pct")
        load_str = f'{h["load_pct"]}%' if "load_pct" in h else "\u2014"
        rpm_str = str(h.get("rpm", "\u2014"))
        seat_str = h.get("seatbelt", "\u2014")
        if seat_str == "Unbuckled":
            seat_str = '<font color="#e94560"><b>No</b></font>'
        elif seat_str == "Buckled":
            seat_str = '<font color="#22c55e">Yes</font>'

        eng_on = h.get("engine_on", False)
        eng_str = ('<font color="#22c55e"><b>ON</b></font>' if eng_on
                   else '<font color="#94a3b8">OFF</font>')
        lr_txt, lr_clr = _latest_reading(h)
        lr_str = f'<font color="{lr_clr}"><b>{lr_txt}</b></font>'

        row = [Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"])]
        if show_org:
            row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
        row.extend([
            Paragraph(eng_str, styles["CellText"]),
            Paragraph(batt_str, styles["CellText"]),
            Paragraph(oil_str, styles["CellText"]),
            Paragraph(cool_str, styles["CellText"]),
            Paragraph(def_str, styles["CellText"]),
            Paragraph(load_str, styles["CellText"]),
            Paragraph(seat_str, styles["CellText"]),
            Paragraph(rpm_str, styles["CellText"]),
            Paragraph(lr_str, styles["CellText"]),
        ])
        table_data.append(row)

    t = Table(table_data, colWidths=col_w, repeatRows=1)
    num_rows = len(table_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    for i in range(1, num_rows):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG))
        v_item = vehicles[i - 1] if i - 1 < len(vehicles) else {}
        if v_item.get("_health_alerts"):
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#fef2f2")))
    t.setStyle(TableStyle(cmds))
    story.append(t)

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate fleet weather report
# ══════════════════════════════════════════════════════════════════

C_FREEZE  = colors.HexColor("#3b82f6")   # blue — freezing
C_COOL    = colors.HexColor("#22c55e")   # green — comfortable
C_WARM    = colors.HexColor("#f59e0b")   # amber — warm
C_HOT     = colors.HexColor("#e94560")   # red — extreme heat


def generate_weather_pdf(
    vehicles: list[dict],
    company_filter: str | None = None,
) -> io.BytesIO:
    """Generate a fleet weather / ambient conditions PDF report."""
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    _now_dt = datetime.now(_TZ_ET)
    now = _now_dt.strftime(f"%B %d, %Y  %I:%M %p {_now_dt.tzname()}")
    page_w = 7.1 * inch

    # ── Header ───────────────────────────────────────────────────
    subtitle = "Fleet Weather & Ambient Conditions"
    if company_filter:
        subtitle = f"{COMPANY_DISPLAY.get(company_filter, company_filter)} — Weather Report"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Compute stats ────────────────────────────────────────────
    temps = [v["_weather"]["temp_f"] for v in vehicles
             if v.get("_weather", {}).get("temp_f") is not None]
    freezing = sum(1 for t in temps if t <= 32)
    cold = sum(1 for t in temps if 32 < t <= 50)
    moderate = sum(1 for t in temps if 50 < t < 85)
    hot = sum(1 for t in temps if t >= 85)
    avg_temp = sum(temps) / len(temps) if temps else 0
    min_temp = min(temps) if temps else 0
    max_temp = max(temps) if temps else 0

    # ── Summary dashboard ────────────────────────────────────────
    _add_section_header(story, styles, "WEATHER OVERVIEW", C_ACCENT)

    row1 = [[
        _mini_stat(styles, str(len(vehicles)), "Trucks", C_ACCENT),
        _mini_stat(styles, f"{avg_temp:.0f}\u00b0F", "Avg Temp", C_HEALTH_BLUE),
        _mini_stat(styles, f"{min_temp:.0f}\u00b0F", "Coldest", C_FREEZE),
        _mini_stat(styles, f"{max_temp:.0f}\u00b0F", "Hottest", C_HOT),
        _mini_stat(styles, str(freezing), "Freezing", C_FREEZE),
        _mini_stat(styles, str(hot), "Hot \u226585\u00b0F", C_HOT),
    ]]
    t1 = Table(row1, colWidths=[page_w / 6] * 6, rowHeights=[52])
    t1.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t1)
    story.append(Spacer(1, 14))

    # ── Risk assessment ──────────────────────────────────────────
    if freezing > 0:
        _add_section_header(story, styles,
                            f"FREEZING RISK  |  {freezing} truck{'s' if freezing != 1 else ''}  \u226432\u00b0F",
                            C_FREEZE)
        risk_lines = [
            "\u2022 Air brake lines may freeze \u2014 check air dryer & drain valves",
            "\u2022 Diesel fuel gelling risk \u2014 verify anti-gel additive",
            "\u2022 Tire pressure drops ~1 PSI per 10\u00b0F \u2014 check tire pressure",
            "\u2022 Battery capacity reduced \u2014 monitor voltage",
            "\u2022 DEF may crystallize below 12\u00b0F \u2014 check DEF tank heater",
        ]
        for line in risk_lines:
            story.append(Paragraph(line, styles["TruckMeta"]))
        story.append(Spacer(1, 8))

    if hot > 0:
        _add_section_header(story, styles,
                            f"HEAT RISK  |  {hot} truck{'s' if hot != 1 else ''}  \u226585\u00b0F",
                            C_HOT)
        risk_lines = [
            "\u2022 Tire blowout risk increases \u2014 check tire conditions",
            "\u2022 Engine coolant stress \u2014 monitor coolant temp closely",
            "\u2022 A/C load increases fuel consumption",
            "\u2022 Brake fade risk on long grades \u2014 monitor brake temps",
        ]
        for line in risk_lines:
            story.append(Paragraph(line, styles["TruckMeta"]))
        story.append(Spacer(1, 8))

    # ── Vehicle table ────────────────────────────────────────────
    companies_seen: list[str] = []
    for v in vehicles:
        o = v.get("_org", "")
        if o and o not in companies_seen:
            companies_seen.append(o)

    show_org = len(companies_seen) > 1 and not company_filter

    _add_section_header(
        story, styles,
        f"FLEET CONDITIONS  |  {len(vehicles)} trucks",
        C_HEALTH_BLUE,
    )

    if show_org:
        col_w = [0.60*inch, 0.55*inch, 0.75*inch, 0.75*inch,
                 0.80*inch, 0.85*inch, 2.80*inch]
        hdr = [
            Paragraph("<b>Truck</b>",     styles["CellBold"]),
            Paragraph("<b>Co.</b>",       styles["CellBold"]),
            Paragraph("<b>Temp \u00b0F</b>",  styles["CellBold"]),
            Paragraph("<b>Temp \u00b0C</b>",  styles["CellBold"]),
            Paragraph("<b>Baro inHg</b>", styles["CellBold"]),
            Paragraph("<b>Updated</b>",   styles["CellBold"]),
            Paragraph("<b>Location</b>",  styles["CellBold"]),
        ]
    else:
        col_w = [0.65*inch, 0.80*inch, 0.80*inch, 0.85*inch,
                 0.90*inch, 3.10*inch]
        hdr = [
            Paragraph("<b>Truck</b>",     styles["CellBold"]),
            Paragraph("<b>Temp \u00b0F</b>",  styles["CellBold"]),
            Paragraph("<b>Temp \u00b0C</b>",  styles["CellBold"]),
            Paragraph("<b>Baro inHg</b>", styles["CellBold"]),
            Paragraph("<b>Updated</b>",   styles["CellBold"]),
            Paragraph("<b>Location</b>",  styles["CellBold"]),
        ]

    table_data = [hdr]
    for v in vehicles:
        w = v.get("_weather", {})
        temp_f = w.get("temp_f")
        temp_c = w.get("temp_c")
        baro = w.get("baro_inhg")

        # Color-code temperature
        if temp_f is None:
            temp_f_str = "\u2014"
            temp_c_str = "\u2014"
        else:
            if temp_f <= 32:
                t_clr = "#3b82f6"    # blue
            elif temp_f <= 50:
                t_clr = "#06b6d4"    # cyan
            elif temp_f < 85:
                t_clr = "#22c55e"    # green
            elif temp_f < 100:
                t_clr = "#f59e0b"    # amber
            else:
                t_clr = "#e94560"    # red
            temp_f_str = f'<font color="{t_clr}"><b>{temp_f:.0f}\u00b0F</b></font>'
            temp_c_str = f'<font color="{t_clr}"><b>{temp_c:.0f}\u00b0C</b></font>'

        baro_str = f"{baro:.2f}" if baro is not None else "\u2014"

        # Best available time
        t_time = w.get("temp_time", w.get("baro_time", ""))
        time_str = _fmt_time(t_time)
        loc_str = _short_location(v.get("location", {}))

        row = [Paragraph(f"<b>#{v.get('name', '?')}</b>", styles["CellBold"])]
        if show_org:
            row.append(Paragraph(v.get("_org", ""), styles["CellText"]))
        row.extend([
            Paragraph(temp_f_str, styles["CellText"]),
            Paragraph(temp_c_str, styles["CellText"]),
            Paragraph(baro_str, styles["CellText"]),
            Paragraph(time_str, styles["CellText"]),
            Paragraph(loc_str, styles["CellText"]),
        ])
        table_data.append(row)

    t = Table(table_data, colWidths=col_w, repeatRows=1)
    num_rows = len(table_data)
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    # Color-code rows by temperature
    for i in range(1, num_rows):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG))
        vw = vehicles[i - 1].get("_weather", {}) if i - 1 < len(vehicles) else {}
        tf = vw.get("temp_f")
        if tf is not None and tf <= 32:
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#eff6ff")))   # light blue tint
        elif tf is not None and tf >= 100:
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#fef2f2")))   # light red tint
    t.setStyle(TableStyle(cmds))
    story.append(t)

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# CAMERA CHECK PDF
# ══════════════════════════════════════════════════════════════════

_CAM_STATUS_COLORS = {
    "PROBLEM": C_RED,
    "WARNING": C_ORANGE,
    "OK": C_GREEN,
    "ERROR": C_GRAY,
}


def generate_camera_check_pdf(results: list[dict]) -> io.BytesIO:
    """Generate a PDF report for camera check results.

    Layout:
      1. Header banner with stats
      2. Summary table (all vehicles, text-only)
      3. Image gallery: dashcam snapshot + analysis for vehicles with footage
    """
    buf = io.BytesIO()
    now = datetime.now(_TZ_ET).strftime("%B %d, %Y  %I:%M %p %Z")

    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "CamTitle", parent=styles["Heading1"],
        fontSize=18, textColor=C_WHITE, alignment=TA_CENTER,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "CamSubtitle", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#94a3b8"),
        alignment=TA_CENTER, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "CamCell", parent=styles["Normal"],
        fontSize=8, leading=10,
    ))
    styles.add(ParagraphStyle(
        "CamImgLabel", parent=styles["Normal"],
        fontSize=9, leading=11, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        "CamImgSummary", parent=styles["Normal"],
        fontSize=8, leading=10, textColor=colors.HexColor("#374151"),
    ))
    styles.add(ParagraphStyle(
        "CamSectionHead", parent=styles["Heading2"],
        fontSize=13, textColor=C_ACCENT, spaceBefore=14, spaceAfter=6,
    ))
    if "FooterText" not in [s.name for s in styles.byName.values()]:
        styles.add(ParagraphStyle(
            "FooterText", parent=styles["Normal"],
            fontSize=7, textColor=C_GRAY, alignment=TA_CENTER,
        ))

    story = []

    # ── Header banner ────────────────────────────────────────────
    problems = sum(1 for r in results if r.get("status") == "PROBLEM")
    warnings = sum(1 for r in results if r.get("status") == "WARNING")
    ok_count = sum(1 for r in results if r.get("status") == "OK")
    errors = sum(1 for r in results if r.get("status") == "ERROR")
    no_data = sum(1 for r in results if r.get("status") == "NO_DATA")

    header_bg = C_HEADER_BG if not problems else colors.HexColor("#7f1d1d")
    header_data = [[
        Paragraph("Camera Check Report", styles["CamTitle"]),
    ]]
    ht = Table(header_data, colWidths=[9.5 * inch])
    ht.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), header_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(ht)

    checked = len(results) - no_data
    story.append(Paragraph(
        f"{checked} camera(s) checked  |  "
        f"Problems: {problems}  Warnings: {warnings}  OK: {ok_count}  Errors: {errors}  |  {now}",
        styles["CamSubtitle"],
    ))
    story.append(Spacer(1, 12))

    # ── Data table ───────────────────────────────────────────────
    col_widths = [1.0 * inch, 0.7 * inch, 1.0 * inch, 0.7 * inch,
                  0.7 * inch, 1.0 * inch, 1.0 * inch, 0.8 * inch, 2.6 * inch]
    header_row = [
        Paragraph("<b>Vehicle</b>", styles["CamCell"]),
        Paragraph("<b>Camera</b>", styles["CamCell"]),
        Paragraph("<b>Driver</b>", styles["CamCell"]),
        Paragraph("<b>Status</b>", styles["CamCell"]),
        Paragraph("<b>Quality</b>", styles["CamCell"]),
        Paragraph("<b>Alignment</b>", styles["CamCell"]),
        Paragraph("<b>Obstruction</b>", styles["CamCell"]),
        Paragraph("<b>Event Time</b>", styles["CamCell"]),
        Paragraph("<b>Summary</b>", styles["CamCell"]),
    ]
    data = [header_row]

    for r in results:
        event_time = ""
        if r.get("event_time"):
            event_time = _fmt_time(r["event_time"])

        status_color = _CAM_STATUS_COLORS.get(r.get("status", "OK"), C_GRAY)
        status_text = (
            f'<font color="{status_color.hexval()}">'
            f'<b>{r.get("status", "?")}</b></font>'
        )

        data.append([
            Paragraph(f"#{r.get('vehicle', '?')}", styles["CamCell"]),
            Paragraph(r.get("camera_type", "forward"), styles["CamCell"]),
            Paragraph(r.get("driver", "\u2014"), styles["CamCell"]),
            Paragraph(status_text, styles["CamCell"]),
            Paragraph(r.get("quality", "?"), styles["CamCell"]),
            Paragraph(r.get("alignment", "?"), styles["CamCell"]),
            Paragraph(r.get("obstruction", "?"), styles["CamCell"]),
            Paragraph(event_time, styles["CamCell"]),
            Paragraph(r.get("summary", "")[:120], styles["CamCell"]),
        ])

    cam_t = Table(data, colWidths=col_widths, repeatRows=1)
    num_rows = len(data)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BOX", (0, 0), (-1, -1), 0.8, C_ACCENT),
    ]
    # Color-code rows by status
    for i in range(1, num_rows):
        r = results[i - 1] if i - 1 < len(results) else {}
        status = r.get("status", "OK")
        if status == "PROBLEM":
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#fde2e4")))
        elif status == "WARNING":
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#fff0e0")))
        elif i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG))
    cam_t.setStyle(TableStyle(cmds))
    story.append(cam_t)

    # ── Image gallery: dashcam snapshots ─────────────────────────
    # Show snapshots for vehicles that have footage, grouped in 2-column
    # layout. Problems and warnings first, then OK vehicles.
    with_images = [r for r in results
                   if r.get("image_bytes") and len(r["image_bytes"]) > 100]
    if with_images:
        story.append(PageBreak())
        story.append(Paragraph("Dashcam Snapshots", styles["CamSectionHead"]))
        story.append(Spacer(1, 4))

        # Build image cards in pairs (2 per row, landscape layout)
        IMG_W = 3.8 * inch
        IMG_H = 2.4 * inch
        TEXT_W = IMG_W
        GAP = 0.4 * inch  # space between the two columns

        pairs = []
        for i in range(0, len(with_images), 2):
            pairs.append(with_images[i:i + 2])

        for pair in pairs:
            row_cells = []
            for r in pair:
                # Build one image card (image + label underneath)
                status_color = _CAM_STATUS_COLORS.get(
                    r.get("status", "OK"), C_GRAY)
                status_str = r.get("status", "?")
                status_html = (
                    f'<font color="{status_color.hexval()}">'
                    f'<b>{status_str}</b></font>'
                )
                driver_str = r.get("driver") or "\u2014"

                img_buf = io.BytesIO(r["image_bytes"])
                img = RLImage(img_buf, width=IMG_W, height=IMG_H)
                img.hAlign = "LEFT"

                # Label line: status icon + vehicle + driver + time
                event_time = ""
                if r.get("event_time"):
                    event_time = f" \u2022 {_fmt_time(r['event_time'])}"
                label = Paragraph(
                    f'{status_html}  <b>#{r.get("vehicle", "?")}</b>'
                    f'  \u2022 {driver_str}{event_time}',
                    styles["CamImgLabel"],
                )
                summary = Paragraph(
                    r.get("summary", "")[:200],
                    styles["CamImgSummary"],
                )
                # Stack image + label + summary into a mini table
                card_data = [[img], [label], [summary]]
                card = Table(card_data, colWidths=[TEXT_W])
                card_cmds = [
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
                # Add colored left border for problems/warnings
                if status_str == "PROBLEM":
                    card_cmds.append(
                        ("LINEBEFOREDECORATE", (0, 0), (0, -1),
                         3, colors.HexColor("#e94560")))
                elif status_str == "WARNING":
                    card_cmds.append(
                        ("LINEBEFOREDECORATE", (0, 0), (0, -1),
                         3, colors.HexColor("#f59e0b")))
                card.setStyle(TableStyle(card_cmds))
                row_cells.append(card)

            # Pad to 2 columns if odd number of images
            if len(row_cells) == 1:
                row_cells.append("")

            row_table = Table(
                [row_cells],
                colWidths=[TEXT_W + 0.1 * inch, TEXT_W + 0.1 * inch],
                spaceBefore=6, spaceAfter=10,
            )
            row_table.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(KeepTogether([row_table]))

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf
