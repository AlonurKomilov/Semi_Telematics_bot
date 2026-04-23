"""Shared PDF infrastructure: styles, colors, helpers, builders."""

import io
from datetime import datetime, timezone, timedelta
from constants import TZ_ET as _TZ_ET
from reportlab.lib import colors

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.pdfgen.canvas import Canvas

from core.context import get_company_display


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
        name = get_company_display().get(code, code)
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
        f"Generated by 4truck  \u2022  {date_str}  \u2022  Data from Samsara API",
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



# Extra colors for efficiency report
C_BLUE = colors.HexColor("#3b82f6")
C_IDLE = colors.HexColor("#f59e0b")
C_EFF_GREEN = colors.HexColor("#22c55e")
C_EFF_AMBER = colors.HexColor("#f59e0b")
C_EFF_BLUE  = colors.HexColor("#3b82f6")

# Health report colors
C_HEALTH_GOOD = colors.HexColor("#22c55e")
C_HEALTH_WARN = colors.HexColor("#f59e0b")
C_HEALTH_CRIT = colors.HexColor("#e94560")
C_HEALTH_BLUE = colors.HexColor("#3b82f6")

# Weather report colors
C_FREEZE  = colors.HexColor("#3b82f6")   # blue — freezing
C_COOL    = colors.HexColor("#22c55e")   # green — comfortable
C_WARM    = colors.HexColor("#f59e0b")   # amber — warm
C_HOT     = colors.HexColor("#e94560")   # red — extreme heat


__all__ = [
    "io",
    "datetime",
    "timezone",
    "timedelta",
    "colors",
    "letter",
    "landscape",
    "inch",
    "getSampleStyleSheet",
    "ParagraphStyle",
    "SimpleDocTemplate",
    "Paragraph",
    "Spacer",
    "Table",
    "TableStyle",
    "HRFlowable",
    "KeepTogether",
    "PageBreak",
    "TA_LEFT",
    "TA_CENTER",
    "TA_RIGHT",
    "Drawing",
    "Rect",
    "get_company_display",
    "_TZ_ET",
    "C_DARK",
    "C_HEADER_BG",
    "C_ACCENT",
    "C_RED",
    "C_ORANGE",
    "C_YELLOW",
    "C_GREEN",
    "C_GRAY",
    "C_LIGHT_BG",
    "C_WHITE",
    "C_BLACK",
    "SEV_STOP",
    "SEV_PROTECT",
    "SEV_EMIS",
    "SEV_WARN",
    "SEV_MINOR",
    "ROW_MOST_SEVERE",
    "ROW_MODERATE",
    "ROW_LEAST",
    "ROW_FAILURE",
    "ROW_DEFAULT",
    "ROW_ALT",
    "C_MINOR_BG",
    "DOT_RED",
    "DOT_ORANGE",
    "DOT_YELLOW",
    "DOT_GRAY",
    "C_ORG_BANNER",
    "C_CRIT_HEADER",
    "C_CRIT_BANNER",
    "C_BLUE",
    "C_IDLE",
    "C_EFF_GREEN",
    "C_EFF_AMBER",
    "C_EFF_BLUE",
    "C_HEALTH_GOOD",
    "C_HEALTH_WARN",
    "C_HEALTH_CRIT",
    "C_HEALTH_BLUE",
    "C_FREEZE",
    "C_COOL",
    "C_WARM",
    "C_HOT",
    "_fmt_time",
    "_fmt_time_with_age",
    "_occ_trend",
    "_short_location",
    "_severity_label",
    "_light_badges_text",
    "_sev_rank",
    "_safe",
    "_spn_display",
    "_fmi_display",
    "_dtc_severity_info",
    "_build_styles",
    "compute_stats",
    "_NumberedCanvas",
    "_add_header",
    "_add_summary_dashboard",
    "_add_company_breakdown_table",
    "_add_company_banner",
    "_add_section_header",
    "_add_footer",
    "_mini_stat",
    "_build_toc",
    "_make_section_header_table",
    "_build_action_items",
    "_drive_idle_bar",
    "_build_truck_card",
    "_add_fleet_health_overview",
    "_add_fleet_status_grid",
]
