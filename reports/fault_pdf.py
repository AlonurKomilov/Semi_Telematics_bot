"""Fault and critical report PDF generators."""
from .pdf_base import *  # noqa: F403,F401

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

