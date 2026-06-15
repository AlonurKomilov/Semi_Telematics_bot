"""Camera check PDF generator."""
from capabilities.reporting.pdf_base import *  # noqa: F403,F401

_CAM_STATUS_COLORS = {
    "PROBLEM": C_RED,
    "WARNING": C_ORANGE,
    "OK": C_GREEN,
    "ERROR": C_GRAY,
}



def generate_camera_check_pdf(results: list[dict]) -> io.BytesIO:
    """Generate a PDF report for camera check results."""
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
    story.append(Paragraph(
        f"{len(results)} camera(s) checked  |  "
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

    # ── Footer ───────────────────────────────────────────────────
    _add_footer(story, styles, now)

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf
