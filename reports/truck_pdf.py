"""Single truck detail PDF generator."""
from .pdf_base import *  # noqa: F403,F401

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


