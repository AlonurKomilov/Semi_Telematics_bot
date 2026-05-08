"""Stakeholder Risk Summary PDF generator.

Universal per-subject risk profile report.  Output is shaped by the
``audience`` parameter — every audience shares the same data assembly
(``RiskProfile``) but renders a different subset of sections, branding,
and disclaimers via :mod:`capabilities.reporting.audiences`.
"""
from __future__ import annotations

import io
from typing import Optional

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, KeepTogether, HRFlowable,
)

from .audiences import (
    AudienceConfig, get_audience_config,
    SECTION_COMPARISON, SECTION_COVER, SECTION_DATA_SOURCES,
    SECTION_DRIVER_INFO, SECTION_INCIDENTS, SECTION_MAINTENANCE,
    SECTION_METHODOLOGY, SECTION_PILLARS, SECTION_SCORE_GAUGE,
    SECTION_TREND, SECTION_VEHICLE_INFO,
)
from .pdf_base import (
    C_ACCENT, C_BLACK, C_DARK, C_GRAY, C_GREEN, C_LIGHT_BG,
    C_ORANGE, C_RED, C_WHITE, C_YELLOW,
    _NumberedCanvas, _add_header, _build_styles, _mini_stat,
)
from .risk_profile import RiskProfile


# ── Style helpers ─────────────────────────────────────────────────

def _grade_color(score: Optional[int]):
    if score is None:
        return C_GRAY
    if score >= 90:
        return C_GREEN
    if score >= 75:
        return C_YELLOW
    if score >= 60:
        return C_ORANGE
    return C_RED


def _anonymise_driver(name: str, driver_id: str) -> str:
    """Replace driver name with an anonymised tag for broker packets."""
    tail = (driver_id or "")[-4:].upper() or "0000"
    return f"DRV-{tail}"


# ── Section renderers ────────────────────────────────────────────

def _render_cover(story, styles, profile: RiskProfile, cfg: AudienceConfig,
                  account_label: str) -> None:
    title = "STAKEHOLDER RISK SUMMARY"
    subtitle = cfg.label
    date_str = profile.generated_at.strftime("%B %d, %Y · %H:%M UTC")
    _add_header(story, styles, title, subtitle, date_str)

    subject_label = profile.subject_display
    if profile.subject_type == "driver" and not cfg.show_pii_driver_name:
        subject_label = _anonymise_driver(profile.subject_display, profile.subject_id)

    info_rows = [
        ["Subject", f"{profile.subject_type.title()}: {subject_label}"],
        ["Account", account_label or str(profile.account_id)],
        ["Window",  f"Last {profile.days} day(s)"],
        ["Generated", date_str],
    ]
    if profile.company:
        info_rows.insert(2, ["Company", profile.company])

    t = Table(info_rows, colWidths=[1.4 * inch, 5.7 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), C_LIGHT_BG),
        ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",   (0, 0), (-1, -1), C_DARK),
        ("BOX",         (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",   (0, 0), (-1, -1), 0.25, C_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))


def _render_score_gauge(story, styles, profile: RiskProfile) -> None:
    score = profile.total_score if profile.total_score is not None else 0
    color = _grade_color(profile.total_score)
    grade = profile.grade or "—"

    _section_header(story, styles, "OVERALL RISK SCORE")

    # A wide horizontal bar with the score value drawn on top.
    page_w = 7.1 * inch
    d = Drawing(page_w, 56)
    d.add(Rect(0, 18, page_w, 24, fillColor=C_LIGHT_BG, strokeColor=C_GRAY,
               strokeWidth=0.5))
    fill_w = max(0.0, min(1.0, score / 100.0)) * page_w
    d.add(Rect(0, 18, fill_w, 24, fillColor=color, strokeColor=None))
    d.add(String(page_w / 2, 24, f"{score}/100  ·  Grade {grade}",
                 fontName="Helvetica-Bold", fontSize=12, fillColor=C_DARK,
                 textAnchor="middle"))
    story.append(d)
    story.append(Spacer(1, 6))

    # Mini-stats row: percentile + fleet size + window
    pct = profile.fleet_percentile if profile.fleet_percentile is not None else "—"
    median = (
        f"{profile.fleet_median:.0f}"
        if isinstance(profile.fleet_median, (int, float)) else "—"
    )
    cells = [[
        _mini_stat(styles, str(pct), "Fleet percentile", C_ACCENT),
        _mini_stat(styles, median, "Fleet median", C_GRAY),
        _mini_stat(styles, str(profile.fleet_size), "Fleet size", C_GRAY),
        _mini_stat(styles, f"{profile.days}d", "Window", C_GRAY),
    ]]
    t = Table(cells, colWidths=[page_w / 4] * 4, rowHeights=[44])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.5, C_GRAY),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))


def _render_pillars(story, styles, profile: RiskProfile) -> None:
    if not profile.pillars:
        return
    _section_header(story, styles, "PILLAR BREAKDOWN")

    rows = [["Pillar", "Score", "Cap"]]
    for name, value in profile.pillars.items():
        if isinstance(value, dict):
            subtotal = value.get("subtotal")
            cap = value.get("cap")
            score_str = (
                f"{float(subtotal):.1f}"
                if isinstance(subtotal, (int, float)) else "—"
            )
            cap_str = (
                f"{float(cap):.0f}"
                if isinstance(cap, (int, float)) else "—"
            )
        else:
            try:
                score_str = f"{float(value):.1f}"
            except (TypeError, ValueError):
                score_str = "—"
            cap_str = "—"
        rows.append([
            Paragraph(str(name).replace("_", " ").title(), styles["CellText"]),
            Paragraph(score_str, styles["CellBold"]),
            Paragraph(cap_str, styles["CellText"]),
        ])
    page_w = 7.1 * inch
    t = Table(rows, colWidths=[page_w * 0.55, page_w * 0.25, page_w * 0.20])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",  (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.25, C_GRAY),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))


def _render_trend(story, styles, profile: RiskProfile) -> None:
    if len(profile.daily_history) < 2:
        return
    _section_header(story, styles, "SCORE TREND")

    series = [
        int(r.get("total_score") or 0) for r in profile.daily_history
    ]
    page_w = 7.1 * inch
    d = Drawing(page_w, 110)
    chart = HorizontalLineChart()
    chart.x = 30
    chart.y = 12
    chart.height = 80
    chart.width = page_w - 60
    chart.data = [series]
    chart.lines[0].strokeColor = C_ACCENT
    chart.lines[0].strokeWidth = 1.4
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 25
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fillColor = C_GRAY
    chart.categoryAxis.categoryNames = [
        str(r.get("snapshot_date", ""))[5:] for r in profile.daily_history
    ]
    d.add(chart)
    story.append(d)
    story.append(Spacer(1, 8))


def _render_incidents(story, styles, profile: RiskProfile,
                      cfg: AudienceConfig) -> None:
    _section_header(story, styles, "SAFETY INCIDENTS")
    if not profile.safety_events:
        story.append(Paragraph(
            "No safety events recorded in the reporting window.",
            styles["CellText"],
        ))
        story.append(Spacer(1, 8))
        return

    rows = [["Date", "Type", "Severity", "Vehicle", "Speed"]]
    if cfg.show_video_links:
        rows[0].append("Video")
    for e in profile.safety_events[:50]:
        occurred = str(e.get("occurred_at") or "")[:16].replace("T", " ")
        speed = e.get("speed_mph")
        speed_s = f"{float(speed):.0f} mph" if isinstance(speed, (int, float)) else "—"
        row = [
            Paragraph(occurred, styles["CellText"]),
            Paragraph(str(e.get("event_type") or "—"), styles["CellText"]),
            Paragraph(str(e.get("severity") or "—"), styles["CellText"]),
            Paragraph(str(e.get("vehicle_name") or e.get("vehicle_id") or "—"),
                      styles["CellText"]),
            Paragraph(speed_s, styles["CellText"]),
        ]
        if cfg.show_video_links:
            url = str(e.get("video_url") or "").strip()
            if url:
                row.append(Paragraph(
                    f'<link href="{url}" color="#0f3460">view</link>',
                    styles["CellText"],
                ))
            else:
                row.append(Paragraph("—", styles["CellText"]))
        rows.append(row)

    if cfg.show_video_links:
        widths = [1.10, 1.30, 0.90, 1.40, 0.70, 1.70]
    else:
        widths = [1.20, 1.60, 1.00, 2.20, 1.10]
    t = Table(rows, colWidths=[w * inch for w in widths])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 10))


def _render_maintenance(story, styles, profile: RiskProfile) -> None:
    _section_header(story, styles, "MAINTENANCE STATUS")
    if profile.subject_type != "vehicle":
        story.append(Paragraph(
            "Maintenance section applies only to vehicle subjects.",
            styles["CellText"],
        ))
        story.append(Spacer(1, 8))
        return

    if not profile.overdue_tasks and not profile.pending_tasks:
        story.append(Paragraph(
            "No outstanding maintenance tasks.", styles["CellText"],
        ))
        story.append(Spacer(1, 8))
        return

    rows = [["Status", "Task", "Description", "Due"]]
    for t_ in profile.overdue_tasks:
        rows.append([
            Paragraph('<font color="#e94560">OVERDUE</font>', styles["CellBold"]),
            Paragraph(str(t_.get("task_type") or "—"), styles["CellText"]),
            Paragraph(str(t_.get("description") or "")[:80], styles["CellText"]),
            Paragraph(str(t_.get("due_date") or t_.get("due_miles") or "—"),
                      styles["CellText"]),
        ])
    for t_ in profile.pending_tasks:
        rows.append([
            Paragraph("pending", styles["CellText"]),
            Paragraph(str(t_.get("task_type") or "—"), styles["CellText"]),
            Paragraph(str(t_.get("description") or "")[:80], styles["CellText"]),
            Paragraph(str(t_.get("due_date") or t_.get("due_miles") or "—"),
                      styles["CellText"]),
        ])
    page_w = 7.1 * inch
    t = Table(rows, colWidths=[
        page_w * 0.14, page_w * 0.22, page_w * 0.47, page_w * 0.17,
    ])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 10))


def _render_comparison(story, styles, profile: RiskProfile) -> None:
    if profile.fleet_median is None or profile.total_score is None:
        return
    _section_header(story, styles, "FLEET COMPARISON")

    delta = float(profile.total_score) - float(profile.fleet_median)
    direction = "above" if delta >= 0 else "below"
    pct = profile.fleet_percentile if profile.fleet_percentile is not None else 0
    text = (
        f"This subject scored <b>{profile.total_score}</b> versus the fleet "
        f"median of <b>{profile.fleet_median:.0f}</b> "
        f"({abs(delta):.1f} points {direction}). "
        f"Ranked at the <b>{pct}th</b> percentile across "
        f"{profile.fleet_size} subjects in the {profile.days}-day window."
    )
    story.append(Paragraph(text, styles["CellText"]))
    story.append(Spacer(1, 10))


def _render_methodology(story, styles, profile: RiskProfile) -> None:
    _section_header(story, styles, "METHODOLOGY & DATA SOURCES")
    body = (
        "<b>Score composition.</b> The total score is a weighted blend of four "
        "pillars: <i>Safety</i> (Samsara harsh events &amp; collisions), "
        "<i>Efficiency</i> (idle, fuel-economy, anticipation), <i>Compliance</i> "
        "(HOS &amp; DVIR), and <i>Maintenance</i> (overdue tasks &amp; DTC "
        "severity). Each pillar is computed from rule-based events with a "
        "decay window over the reporting period.<br/><br/>"
        "<b>Data sources.</b> Telemetry is sourced from the carrier's Samsara "
        "ELD/AOBRD feed in real time (vehicle stats, safety events, HOS) plus "
        "the platform's internal maintenance scheduler. Score events are "
        "persisted in the audit log for historical replay.<br/><br/>"
        "<b>Limitations.</b> Scores reflect telemetric behaviour observed in "
        "the reporting window only; out-of-coverage time, driver swaps, and "
        "vehicle reassignments may dilute or amplify pillar values. Scores "
        "are not actuarial loss predictions and must not be used as the sole "
        "basis for underwriting, pricing, or employment decisions."
    )
    story.append(Paragraph(body, ParagraphStyle(
        "MethodologyBody", parent=styles["CellText"],
        fontSize=8.5, leading=12, alignment=TA_LEFT, textColor=C_BLACK,
    )))
    story.append(Spacer(1, 10))


def _render_data_sources(story, styles, profile: RiskProfile) -> None:
    _section_header(story, styles, "DATA INTEGRITY")
    rows = [
        ["Account ID", str(profile.account_id)],
        ["Subject type", profile.subject_type],
        ["Subject ID", profile.subject_id],
        ["Snapshots used", str(len(profile.daily_history))],
        ["Score events", str(len(profile.score_events))],
        ["Safety events", str(len(profile.safety_events))],
        ["Maintenance tasks", str(
            len(profile.overdue_tasks) + len(profile.pending_tasks)
        )],
        ["Generated at", profile.generated_at.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    page_w = 7.1 * inch
    t = Table(rows, colWidths=[2.0 * inch, page_w - 2.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_LIGHT_BG),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.25, C_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


def _render_vehicle_info(story, styles, profile: RiskProfile) -> None:
    if profile.subject_type != "vehicle":
        return
    _section_header(story, styles, "VEHICLE")
    rows = [
        ["Vehicle", profile.subject_display],
        ["Vehicle ID", profile.subject_id],
    ]
    if profile.company:
        rows.append(["Company", profile.company])
    for k, v in (profile.vehicle_meta or {}).items():
        rows.append([str(k).replace("_", " ").title(), str(v)])
    page_w = 7.1 * inch
    t = Table(rows, colWidths=[1.6 * inch, page_w - 1.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_LIGHT_BG),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.25, C_GRAY),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))


def _render_driver_info(story, styles, profile: RiskProfile,
                        cfg: AudienceConfig) -> None:
    if profile.subject_type != "driver":
        return
    _section_header(story, styles, "DRIVER")
    name = profile.subject_display
    if not cfg.show_pii_driver_name:
        name = _anonymise_driver(profile.subject_display, profile.subject_id)
    rows = [
        ["Driver", name],
        ["Driver ID", profile.subject_id if cfg.show_pii_driver_name else "REDACTED"],
    ]
    if profile.company:
        rows.append(["Company", profile.company])
    for k, v in (profile.driver_meta or {}).items():
        rows.append([str(k).replace("_", " ").title(), str(v)])
    page_w = 7.1 * inch
    t = Table(rows, colWidths=[1.6 * inch, page_w - 1.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_LIGHT_BG),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.25, C_GRAY),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))


# ── Helpers ──────────────────────────────────────────────────────

def _section_header(story, styles, title: str) -> None:
    page_w = 7.1 * inch
    p = Paragraph(
        f"<b>{title}</b>",
        ParagraphStyle(
            f"sec_{id(title)}", parent=styles["SectionHeader"],
            fontSize=11, textColor=C_WHITE, alignment=TA_LEFT,
        ),
    )
    t = Table([[p]], colWidths=[page_w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(KeepTogether([t, Spacer(1, 6)]))


def _table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), C_ACCENT),
        ("TEXTCOLOR",   (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("BOX",         (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",   (0, 0), (-1, -1), 0.25, C_GRAY),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT_BG]),
    ])


# ── Public entry point ───────────────────────────────────────────

def generate_risk_summary_pdf(
    profile: RiskProfile,
    *,
    audience: str = "owner",
    account_label: str = "",
) -> io.BytesIO:
    """Render a Stakeholder Risk Summary PDF for *profile*.

    Args:
        profile: Pre-assembled :class:`RiskProfile`.
        audience: One of ``insurance``, ``owner``, ``broker``, ``auditor``,
            ``payroll``. Unknown values fall back to ``owner``.
        account_label: Optional account display name (for the cover).

    Returns:
        BytesIO buffer positioned at 0, ready to send.
    """
    cfg = get_audience_config(audience)
    styles = _build_styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
        title=f"Risk Summary — {profile.subject_display}",
        author="4truck",
    )

    story: list = []
    renderers = {
        SECTION_COVER:        lambda: _render_cover(story, styles, profile, cfg, account_label),
        SECTION_VEHICLE_INFO: lambda: _render_vehicle_info(story, styles, profile),
        SECTION_DRIVER_INFO:  lambda: _render_driver_info(story, styles, profile, cfg),
        SECTION_SCORE_GAUGE:  lambda: _render_score_gauge(story, styles, profile),
        SECTION_PILLARS:      lambda: _render_pillars(story, styles, profile),
        SECTION_TREND:        lambda: _render_trend(story, styles, profile),
        SECTION_INCIDENTS:    lambda: _render_incidents(story, styles, profile, cfg),
        SECTION_MAINTENANCE:  lambda: _render_maintenance(story, styles, profile),
        SECTION_COMPARISON:   lambda: _render_comparison(story, styles, profile),
        SECTION_METHODOLOGY:  lambda: _render_methodology(story, styles, profile),
        SECTION_DATA_SOURCES: lambda: _render_data_sources(story, styles, profile),
    }
    for sid in cfg.sections:
        fn = renderers.get(sid)
        if fn is not None:
            fn()

    # Disclaimer (always rendered, audience-specific text)
    story.append(HRFlowable(
        width="100%", thickness=0.5, color=C_GRAY,
        spaceBefore=4, spaceAfter=8,
    ))
    story.append(Paragraph(
        f"<b>Disclaimer.</b> {cfg.disclaimer}",
        ParagraphStyle(
            "RiskDisclaimer", parent=styles["FooterText"],
            fontSize=7.5, textColor=C_GRAY, alignment=TA_LEFT, leading=10,
        ),
    ))

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf
