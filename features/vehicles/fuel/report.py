"""Fuel report PDF generator."""
from .pdf_base import *  # noqa: F403,F401

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
        subtitle = f"{get_company_display().get(company_filter, company_filter)} — Fuel Report"
    _add_header(story, styles, "4TRUCK", subtitle, now)

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
            co_name = get_company_display().get(oc, oc)
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

