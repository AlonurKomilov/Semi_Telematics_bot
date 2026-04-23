"""Vehicle health PDF generator."""
from .pdf_base import *  # noqa: F403,F401

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
        subtitle = f"{get_company_display().get(company_filter, company_filter)} — Vehicle Health"
    _add_header(story, styles, "4TRUCK", subtitle, now)

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


