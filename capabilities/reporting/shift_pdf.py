"""Shift handoff report PDF generator."""
from .pdf_base import *  # noqa: F403,F401


def generate_shift_report_pdf(
    *,
    user_name: str,
    working_hours: tuple[int, int] | None,
    queued_alerts: list[dict],
    pending_alerts: list[dict],
    resolved_alerts: list[dict],
    pending_maintenance: list[dict],
    report_date: str,
    timezone_name: str = "America/New_York",
) -> io.BytesIO:
    """Generate a PDF shift handoff report.

    Args:
        user_name: Display name of the recipient.
        working_hours: (start_hour, end_hour) or None.
        queued_alerts: DND-queued alerts from off-hours.
        pending_alerts: Unacknowledged alerts.
        resolved_alerts: Recently resolved alerts.
        pending_maintenance: Pending/overdue maintenance tasks.
        report_date: Date string (e.g. "April 06, 2026").
        timezone_name: User's timezone for display.
    """
    buf = io.BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.55 * inch,
    )

    story: list = []
    page_w = 7.1 * inch

    # ── Header ───────────────────────────────────────────────────
    _add_header(
        story, styles,
        "4TRUCK",
        "Shift Handoff Report",
        report_date,
    )

    # ── Working hours info ───────────────────────────────────────
    info_lines = [f"<b>Prepared for:</b> {user_name}"]
    if working_hours:
        s, e = working_hours
        info_lines.append(
            f"<b>Working hours:</b> {s:02d}:00 – {e:02d}:00"
        )
        info_lines.append(
            f"<b>Off-hours window:</b> {e:02d}:00 – {s:02d}:00"
        )
    tz_short = timezone_name.split("/")[-1].replace("_", " ")
    info_lines.append(f"<b>Timezone:</b> {tz_short}")

    for line in info_lines:
        story.append(Paragraph(line, styles["TruckMeta"]))
    story.append(Spacer(1, 10))

    # ── Summary dashboard ────────────────────────────────────────
    total_queued = len(queued_alerts)
    total_pending = len(pending_alerts)
    total_resolved = len(resolved_alerts)
    total_maint = len(pending_maintenance)

    row = [[
        _mini_stat(styles, str(total_queued), "Off-Hours Alerts", C_ORANGE),
        _mini_stat(styles, str(total_pending), "Pending", C_RED),
        _mini_stat(styles, str(total_resolved), "Resolved", C_GREEN),
        _mini_stat(styles, str(total_maint), "Maintenance", C_ACCENT),
    ]]
    t = Table(row, colWidths=[page_w / 4] * 4, rowHeights=[52])
    t.setStyle(TableStyle([
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
    story.append(t)
    story.append(Spacer(1, 14))

    # ── Section: Off-hours queued alerts ─────────────────────────
    if queued_alerts:
        _shift_section_header(story, styles, page_w,
                            f"📬 Off-Hours Alerts ({total_queued})")

        by_type: dict[str, list[dict]] = {}
        for q in queued_alerts:
            by_type.setdefault(q.get("alert_type", "other"), []).append(q)

        for atype, alerts in by_type.items():
            type_label = {
                "fault": "⚠️ Faults",
                "health": "🏥 Health",
                "fuel": "⛽ Fuel",
                "events": "🚨 Events",
                "geofence": "📍 Geofence",
                "camera": "📷 Camera",
                "parking": "🅿️ Parking",
            }.get(atype, f"🔔 {atype}")
            story.append(Paragraph(
                f"<b>{type_label} ({len(alerts)})</b>",
                styles["CellBold"],
            ))
            story.append(Spacer(1, 4))

            data = [["Vehicle", "Time"]]
            for a in alerts:
                vname = a.get("vehicle_name", "?")
                try:
                    dt = datetime.fromisoformat(a["created_at"])
                    ts = dt.strftime("%H:%M")
                except (ValueError, TypeError, KeyError):
                    ts = "?"
                data.append([vname, ts])

            col_widths = [page_w * 0.6, page_w * 0.4]
            tbl = Table(data, colWidths=col_widths)
            tbl_style = [
                ("BACKGROUND",    (0, 0), (-1, 0), C_ACCENT),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("ALIGN",         (1, 0), (1, -1), "CENTER"),
                ("GRID",          (0, 0), (-1, -1), 0.5, C_GRAY),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
            # Alternate row colors
            for i in range(1, len(data)):
                if i % 2 == 0:
                    tbl_style.append(
                        ("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG)
                    )
            tbl.setStyle(TableStyle(tbl_style))
            story.append(tbl)
            story.append(Spacer(1, 8))

    # ── Section: Pending (unacknowledged) alerts ─────────────────
    if pending_alerts:
        _shift_section_header(story, styles, page_w,
                            f"🔴 Pending Alerts ({total_pending})")
        data = [["Vehicle", "Type", "Since"]]
        for p in pending_alerts:
            vname = p.get("vehicle_name", "?")
            atype = p.get("alert_type", "alert")
            try:
                dt = datetime.fromisoformat(p["created_at"])
                ts = dt.strftime("%b %d %H:%M")
            except (ValueError, TypeError, KeyError):
                ts = "?"
            data.append([vname, atype, ts])

        col_widths = [page_w * 0.4, page_w * 0.25, page_w * 0.35]
        tbl = Table(data, colWidths=col_widths)
        tbl_style = [
            ("BACKGROUND",    (0, 0), (-1, 0), C_RED),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
            ("GRID",          (0, 0), (-1, -1), 0.5, C_GRAY),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for i in range(1, len(data)):
            if i % 2 == 0:
                tbl_style.append(
                    ("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG)
                )
        tbl.setStyle(TableStyle(tbl_style))
        story.append(tbl)
        story.append(Spacer(1, 12))

    # ── Section: Recently resolved alerts ────────────────────────
    if resolved_alerts:
        _shift_section_header(story, styles, page_w,
                            f"✅ Recently Resolved ({total_resolved})")
        data = [["Vehicle", "Type", "Resolved"]]
        for r in resolved_alerts:
            vname = r.get("vehicle_name", "?")
            atype = r.get("alert_type", "alert")
            try:
                ack = r.get("acknowledged_at", r.get("created_at", ""))
                dt = datetime.fromisoformat(ack)
                ts = dt.strftime("%b %d %H:%M")
            except (ValueError, TypeError, KeyError):
                ts = "?"
            data.append([vname, atype, ts])

        col_widths = [page_w * 0.4, page_w * 0.25, page_w * 0.35]
        tbl = Table(data, colWidths=col_widths)
        tbl_style = [
            ("BACKGROUND",    (0, 0), (-1, 0), C_GREEN),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
            ("GRID",          (0, 0), (-1, -1), 0.5, C_GRAY),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for i in range(1, len(data)):
            if i % 2 == 0:
                tbl_style.append(
                    ("BACKGROUND", (0, i), (-1, i), C_LIGHT_BG)
                )
        tbl.setStyle(TableStyle(tbl_style))
        story.append(tbl)
        story.append(Spacer(1, 12))

    # ── Section: Pending maintenance ─────────────────────────────
    if pending_maintenance:
        _shift_section_header(story, styles, page_w,
                            f"🔧 Maintenance Due ({total_maint})")

        overdue = [m for m in pending_maintenance if m.get("status") == "overdue"]
        upcoming = [m for m in pending_maintenance if m.get("status") != "overdue"]

        if overdue:
            story.append(Paragraph(
                f"<b>🔴 Overdue ({len(overdue)})</b>", styles["CellBold"],
            ))
            story.append(Spacer(1, 4))
            data = [["Vehicle", "Task", "Status"]]
            for m in overdue:
                vname = m.get("vehicle_name", "?")
                task = m.get("task_name", m.get("description", "task"))
                data.append([vname, task, "OVERDUE"])
            tbl = Table(data, colWidths=[page_w * 0.3, page_w * 0.5, page_w * 0.2])
            tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#991b1b")),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("GRID",          (0, 0), (-1, -1), 0.5, C_GRAY),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 8))

        if upcoming:
            story.append(Paragraph(
                f"<b>🟡 Pending ({len(upcoming)})</b>", styles["CellBold"],
            ))
            story.append(Spacer(1, 4))
            data = [["Vehicle", "Task", "Status"]]
            for m in upcoming:
                vname = m.get("vehicle_name", "?")
                task = m.get("task_name", m.get("description", "task"))
                status = m.get("status", "pending")
                data.append([vname, task, status])
            tbl = Table(data, colWidths=[page_w * 0.3, page_w * 0.5, page_w * 0.2])
            tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), C_ORANGE),
                ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("GRID",          (0, 0), (-1, -1), 0.5, C_GRAY),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 8))

    # ── Footer note ──────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(
        width="100%", thickness=0.5, color=C_GRAY, spaceAfter=6,
    ))
    story.append(Paragraph(
        "Generated by 4truck • Shift Handoff Report",
        styles["FooterText"],
    ))

    doc.build(story, canvasmaker=_NumberedCanvas)
    buf.seek(0)
    return buf


def _shift_section_header(story, styles, page_w, title):
    """Add a dark section header bar."""
    t = Table(
        [[Paragraph(title, styles["SectionHeader"])]],
        colWidths=[page_w],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_HEADER_BG),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
