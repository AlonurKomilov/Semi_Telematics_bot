"""
PDF Fault Report Generator — professional multi-org fleet fault code reports.
Uses ReportLab to build clean, visual PDF documents.

Two report types:
  • Full Fault Report   — all trucks with active faults
  • Critical Report     — only STOP / PROTECT / EMISSIONS trucks

Multi-org enhancements:
  • Org breakdown summary table (Company | Trucks | Faulted | DTCs)
  • Org-section banners grouping trucks by company
  • Per-org or combined reports

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
from datetime import datetime, timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from samsara_client import ORG_DISPLAY


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

# Severity dot colors for the # column (N5)
DOT_RED    = "#e94560"
DOT_ORANGE = "#f59e0b"
DOT_YELLOW = "#eab308"
DOT_GRAY   = "#94a3b8"

# Org-banner colors
C_ORG_BANNER = colors.HexColor("#1e3a5f")


# ── Helpers ──────────────────────────────────────────────────────

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "\u2014"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso_str


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
    _add("OrgBanner",      parent=base["Heading2"], fontName="Helvetica-Bold",
         fontSize=11, textColor=C_WHITE, spaceBefore=10, spaceAfter=4)
    _add("TruckTitle",     parent=base["Heading3"], fontName="Helvetica-Bold",
         fontSize=11, textColor=C_DARK, spaceBefore=8, spaceAfter=3)
    _add("TruckMeta",      parent=base["Normal"],  fontName="Helvetica",
         fontSize=8.5, textColor=C_GRAY, spaceAfter=2, leading=11)
    _add("CellText",       parent=base["Normal"],  fontName="Helvetica",
         fontSize=7.5, textColor=C_BLACK, leading=9.5)
    _add("CellBold",       parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=7.5, textColor=C_BLACK, leading=9.5)
    _add("FooterText",     parent=base["Normal"],  fontName="Helvetica",
         fontSize=8, textColor=C_GRAY, alignment=TA_CENTER)
    _add("StatNumber",     parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=16, alignment=TA_CENTER, leading=20, spaceBefore=0, spaceAfter=0)
    _add("StatLabel",      parent=base["Normal"],  fontName="Helvetica",
         fontSize=7.5, textColor=C_GRAY, alignment=TA_CENTER,
         leading=9, spaceBefore=0, spaceAfter=0)
    _add("OrgTableHeader", parent=base["Normal"],  fontName="Helvetica-Bold",
         fontSize=8, textColor=C_WHITE, leading=10)
    _add("OrgTableCell",   parent=base["Normal"],  fontName="Helvetica",
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


# Unique style counter to avoid ReportLab name collisions
_style_counter = 0


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate full fault report
# ══════════════════════════════════════════════════════════════════

def generate_fault_report_pdf(
    vehicles_with_faults: list,
    total_vehicles: int,
    org_breakdown: dict[str, dict] | None = None,
    org_filter: str | None = None,
) -> io.BytesIO:
    """Generate PDF fault report.

    Args:
        vehicles_with_faults: List of vehicle dicts (each has ``_org`` key).
        total_vehicles: Grand total of active vehicles scanned.
        org_breakdown: Per-org stats {code: {total, faulted, dtcs}}.
        org_filter: If set, this is a single-org report.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story = []
    now = datetime.now(timezone.utc).strftime("%B %d, %Y  %I:%M %p UTC")
    stats = compute_stats(vehicles_with_faults, total_vehicles)

    # ── Header Banner ────────────────────────────────────────────
    subtitle = "Fleet Fault Code Report"
    if org_filter:
        subtitle = f"{ORG_DISPLAY.get(org_filter, org_filter)} — Fault Report"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Summary Dashboard (M1 fix) ──────────────────────────────
    _add_summary_dashboard(story, styles, stats)

    # ── Org Breakdown Table (multi-org only) ─────────────────────
    if org_breakdown and not org_filter and len(org_breakdown) > 1:
        _add_org_breakdown_table(story, styles, org_breakdown)

    # ── Truck cards grouped by org then severity ─────────────────
    vehicles_with_faults.sort(key=_sev_rank)

    # Group by org
    orgs_present = []
    if org_filter:
        orgs_present = [org_filter]
    else:
        seen = []
        for v in vehicles_with_faults:
            o = v.get("_org", "???")
            if o not in seen:
                seen.append(o)
        orgs_present = seen

    multi_org = len(orgs_present) > 1

    for org_code in orgs_present:
        org_vehicles = [v for v in vehicles_with_faults if v.get("_org") == org_code]
        if not org_vehicles:
            continue

        # Org banner (only for multi-org combined reports)
        if multi_org:
            org_name = ORG_DISPLAY.get(org_code, org_code)
            org_dtcs = sum(len(v.get("_dtcs", [])) for v in org_vehicles)
            _add_org_banner(story, styles, org_code, org_name,
                            len(org_vehicles), org_dtcs)

        # Severity sections within this org
        sections = [
            ("CRITICAL \u2014 Needs Immediate Attention", C_RED,
             [v for v in org_vehicles if _sev_rank(v) <= 2]),
            ("WARNING \u2014 Monitor Closely", C_ORANGE,
             [v for v in org_vehicles if _sev_rank(v) == 3]),
            ("MINOR \u2014 No Dashboard Lights", C_GREEN,
             [v for v in org_vehicles if _sev_rank(v) >= 4]),
        ]

        for title, color, vlist in sections:
            if not vlist:
                continue
            sec_dtcs = sum(len(v.get("_dtcs", [])) for v in vlist)
            count_text = (
                f"{title}  \u2502  {len(vlist)} truck{'s' if len(vlist) != 1 else ''}"
                f"  \u00b7  {sec_dtcs} fault code{'s' if sec_dtcs != 1 else ''}"
            )
            _add_section_header(story, styles, count_text, color)
            for v in vlist:
                story.extend(_build_truck_card(v, styles, show_org=multi_org))
                story.append(Spacer(1, 4))

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# PUBLIC: generate critical-only report
# ══════════════════════════════════════════════════════════════════

def generate_critical_report_pdf(
    critical_vehicles: list,
    total_vehicles: int,
    org_breakdown: dict[str, dict] | None = None,
    org_filter: str | None = None,
) -> io.BytesIO:
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story = []
    now = datetime.now(timezone.utc).strftime("%B %d, %Y  %I:%M %p UTC")

    subtitle = "Critical Fault Report"
    if org_filter:
        subtitle = f"{ORG_DISPLAY.get(org_filter, org_filter)} — Critical Faults"
    _add_header(story, styles, "SEMI TELEMATICS", subtitle, now)

    # ── Mini summary ─────────────────────────────────────────────
    stop = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("stopIsOn"))
    protect = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("protectIsOn"))
    emis = sum(1 for v in critical_vehicles if v.get("_lights", {}).get("emissionsIsOn"))
    total_dtcs = sum(len(v.get("_dtcs", [])) for v in critical_vehicles)

    page_w = 7.1 * inch
    crit_summary = [[
        _mini_stat(styles, str(len(critical_vehicles)), "Trucks", C_RED),
        _mini_stat(styles, str(total_dtcs), "Fault Codes", C_ORANGE),
        _mini_stat(styles, str(stop), "STOP", C_RED),
        _mini_stat(styles, str(protect), "PROTECT", C_ORANGE),
        _mini_stat(styles, str(emis), "EMISSIONS", C_YELLOW),
    ]]
    t = Table(crit_summary, colWidths=[page_w / 5] * 5)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # ── Org Breakdown Table (multi-org only) ─────────────────────
    if org_breakdown and not org_filter and len(org_breakdown) > 1:
        _add_org_breakdown_table(story, styles, org_breakdown)

    # ── Truck cards grouped by org ───────────────────────────────
    critical_vehicles.sort(key=_sev_rank)

    orgs_present = []
    seen = []
    for v in critical_vehicles:
        o = v.get("_org", "???")
        if o not in seen:
            seen.append(o)
    orgs_present = seen
    multi_org = len(orgs_present) > 1

    for org_code in orgs_present:
        org_vehicles = [v for v in critical_vehicles if v.get("_org") == org_code]
        if not org_vehicles:
            continue

        if multi_org:
            org_name = ORG_DISPLAY.get(org_code, org_code)
            org_dtcs = sum(len(v.get("_dtcs", [])) for v in org_vehicles)
            _add_org_banner(story, styles, org_code, org_name,
                            len(org_vehicles), org_dtcs)

        sec_text = (
            f"{len(org_vehicles)} TRUCK{'S' if len(org_vehicles) != 1 else ''} "
            f"NEED ATTENTION  \u2502  "
            f"{sum(len(v.get('_dtcs', [])) for v in org_vehicles)} fault codes"
        )
        _add_section_header(story, styles, sec_text, C_RED)

        for v in org_vehicles:
            story.extend(_build_truck_card(v, styles, show_org=multi_org))
            story.append(Spacer(1, 4))

    _add_footer(story, styles, now)

    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
# SHARED COMPONENTS
# ══════════════════════════════════════════════════════════════════

def _page_number(canvas, doc):
    """N2: Render page number at bottom-right of every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    page_num = canvas.getPageNumber()
    canvas.drawRightString(
        doc.pagesize[0] - 0.5 * inch,
        0.35 * inch,
        f"Page {page_num}",
    )
    canvas.restoreState()


def _add_header(story, styles, title, subtitle, date_str):
    page_w = 7.1 * inch
    rows = [
        [Paragraph(title, styles["ReportTitle"])],
        [Paragraph(subtitle, styles["ReportSubtitle"])],
        [Paragraph(date_str, styles["ReportSubtitle"])],
    ]
    t = Table(rows, colWidths=[page_w])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_HEADER_BG),
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
    story.append(t1)
    story.append(Spacer(1, 4))

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
    story.append(t2)
    story.append(Spacer(1, 10))


# ── Org Breakdown Table (multi-org) ─────────────────────────────

def _add_org_breakdown_table(story, styles, org_breakdown: dict[str, dict]):
    """Render a Company | Trucks | Faulted | DTCs summary table."""
    page_w = 7.1 * inch
    col_widths = [2.8 * inch, 1.2 * inch, 1.2 * inch, 1.2 * inch]

    hdr = [
        Paragraph("<b>Company</b>", styles["OrgTableHeader"]),
        Paragraph("<b>Trucks</b>",  styles["OrgTableHeader"]),
        Paragraph("<b>Faulted</b>", styles["OrgTableHeader"]),
        Paragraph("<b>DTCs</b>",    styles["OrgTableHeader"]),
    ]
    table_data = [hdr]

    grand_total = grand_faulted = grand_dtcs = 0
    for code in sorted(org_breakdown.keys()):
        info = org_breakdown[code]
        name = ORG_DISPLAY.get(code, code)
        total = info.get("total", 0)
        faulted = info.get("faulted", 0)
        dtcs = info.get("dtcs", 0)
        grand_total += total
        grand_faulted += faulted
        grand_dtcs += dtcs
        table_data.append([
            Paragraph(f"{name}  ({code})", styles["OrgTableCell"]),
            Paragraph(str(total),  styles["OrgTableCell"]),
            Paragraph(str(faulted), styles["OrgTableCell"]),
            Paragraph(str(dtcs),   styles["OrgTableCell"]),
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


# ── Org Banner (multi-org) ──────────────────────────────────────

def _add_org_banner(story, styles, org_code, org_name, truck_count, dtc_count):
    """Dark blue banner with org name, code, and counts."""
    page_w = 7.1 * inch
    text = (
        f"  {org_name.upper()}  ({org_code})  \u2502  "
        f"{truck_count} truck{'s' if truck_count != 1 else ''}  \u00b7  "
        f"{dtc_count} fault code{'s' if dtc_count != 1 else ''}"
    )
    t = Table(
        [[Paragraph(text, styles["OrgBanner"])]],
        colWidths=[page_w],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_ORG_BANNER),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 8))
    story.append(t)
    story.append(Spacer(1, 4))


def _add_section_header(story, styles, title, color):
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
    story.append(Spacer(1, 6))


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


# ── M1: Mini stat cell with proper spacing ──────────────────────

def _mini_stat(styles, value: str, label: str, accent: colors.Color):
    global _style_counter
    _style_counter += 1
    uid = _style_counter
    return [
        Paragraph(
            f'<font color="{accent.hexval()}">{value}</font>',
            ParagraphStyle(
                f"mv_{uid}", parent=styles["StatNumber"],
                textColor=accent,
            ),
        ),
        Spacer(1, 2),
        Paragraph(
            label,
            ParagraphStyle(
                f"ml_{uid}", parent=styles["StatLabel"],
            ),
        ),
    ]


# ── Per-Truck Fault Card ────────────────────────────────────────

def _build_truck_card(v: dict, styles, show_org: bool = False) -> list:
    """One truck card: info header + DTC table with per-row severity."""
    elements = []

    lights   = v.get("_lights", {})
    dtcs     = v.get("_dtcs", [])
    loc      = v.get("location", {})
    fuel     = v.get("fuel", {})
    fuel_pct = fuel.get("value")
    fc_time  = v.get("fault_codes", {}).get("time", "") or v.get("_fault_time", "")
    org_code = v.get("_org", "")

    sev_label, card_bg = _severity_label(lights)
    loc_str    = _short_location(loc)
    lights_str = _light_badges_text(lights)
    fuel_str   = f"{fuel_pct}%" if fuel_pct is not None else "\u2014"

    # === Truck info line ===
    org_tag = f"[{org_code}] " if show_org and org_code else ""
    info_text = (
        f'<b>{org_tag}Truck #{v["name"]}</b>  \u00b7  '
        f'{_safe(v.get("year"))} {_safe(v.get("make"))} {_safe(v.get("model"))}  \u00b7  '
        f'VIN: <font face="Courier">{_safe(v.get("vin"), "N/A")}</font>'
    )
    meta_text = (
        f'Plate: {_safe(v.get("license_plate"), "N/A")}  \u00b7  '
        f'Location: {loc_str}  \u00b7  '
        f'Fuel: {fuel_str}  \u00b7  '
        f'Dash: <b>{lights_str}</b>'
    )

    elements.append(Paragraph(info_text, styles["TruckTitle"]))
    elements.append(Paragraph(meta_text, styles["TruckMeta"]))

    # === DTC table ===
    if dtcs:
        col_widths = [
            0.30 * inch,   # # (with severity dot)
            0.50 * inch,   # SPN
            0.35 * inch,   # FMI
            1.85 * inch,   # Issue
            1.70 * inch,   # Severity (M2)
            0.40 * inch,   # Cnt
            1.00 * inch,   # Source (M2)
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
            occ      = _safe(dtc.get("occurrenceCount"), "\u2014")
            source   = _safe(dtc.get("sourceAddressName"), "\u2014")

            row_bg, dot_hex, dot_sym = _dtc_severity_info(
                dtc.get("fmiDescription", "")
            )
            row_severity_info.append((row_bg, dot_hex, dot_sym))

            if len(issue) > 52:
                issue = issue[:49] + "\u2026"
            if len(severity) > 38:
                severity = severity[:35] + "\u2026"
            if len(source) > 26:
                source = source[:23] + "\u2026"

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
                Paragraph(occ,      styles["CellText"]),
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

    if fc_time:
        elements.append(Paragraph(
            f"Last updated: {_fmt_time(fc_time)}", styles["TruckMeta"],
        ))

    return [KeepTogether(elements)]
