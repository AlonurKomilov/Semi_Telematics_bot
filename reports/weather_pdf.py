"""Fleet weather PDF generator."""
from .pdf_base import *  # noqa: F403,F401

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


