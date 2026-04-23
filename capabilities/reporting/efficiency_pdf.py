"""Fleet efficiency PDF generator."""
from .pdf_base import *  # noqa: F403,F401

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
            f"{get_company_display().get(company_filter, company_filter)} — "
            f"Efficiency ({days} Days)"
        )
    _add_header(story, styles, "4TRUCK", subtitle, now)

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
            co_name = get_company_display().get(oc, oc)
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


