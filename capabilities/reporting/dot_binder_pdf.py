"""DOT compliance binder — PDF renderer.

Pure layout module — consumes the dataclass tree from
``dot_binder.build_dot_binder`` and emits a printable PDF.  No DB
calls live here so the renderer is unit-testable with synthetic
fixtures.

Page layout:
  1. Cover page — company name, coverage window, account-wide summary
  2. Per-vehicle section (page break between vehicles):
       * Vehicle header (name, odometer, company)
       * DOT inspections summary (always first if any)
       * Open maintenance tasks
       * Completed services with attestation
       * Work orders with parts + invoice numbers
  3. Final summary page with signature line for the DOT-authorized rep
"""

from __future__ import annotations

import io
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, KeepTogether, PageBreak,
)

from .dot_binder import (
    BinderTask, BinderVehicle, BinderWorkOrder, DOTBinder,
)
from .pdf_base import (
    C_ACCENT, C_DARK, C_GRAY, C_GREEN, C_LIGHT_BG,
    C_ORANGE, C_RED, C_WHITE, C_YELLOW,
    _NumberedCanvas, _add_header, _build_styles, _mini_stat,
)


def _add_binder_styles(styles) -> None:
    """Extend the shared stylesheet with binder-specific paragraph styles.

    Kept separate from ``_build_styles`` in pdf_base so the binder
    additions don't pollute other reports.  Idempotent — guarded by
    ``has`` checks in case the renderer runs twice in one process
    (e.g. test suite).
    """
    if "NormalBody" not in styles:
        styles.add(ParagraphStyle(
            "NormalBody", parent=styles["Normal"], fontName="Helvetica",
            fontSize=9, textColor=C_DARK, leading=12, spaceAfter=2,
        ))
    if "NormalSmall" not in styles:
        styles.add(ParagraphStyle(
            "NormalSmall", parent=styles["Normal"], fontName="Helvetica",
            fontSize=7.5, textColor=C_DARK, leading=10,
        ))
    if "SectionTitle" not in styles:
        styles.add(ParagraphStyle(
            "SectionTitle", parent=styles["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, textColor=C_DARK, spaceBefore=10, spaceAfter=4,
        ))
    if "SectionSubtitle" not in styles:
        styles.add(ParagraphStyle(
            "SectionSubtitle", parent=styles["Heading3"], fontName="Helvetica-Bold",
            fontSize=9, textColor=C_ACCENT, spaceBefore=6, spaceAfter=3,
        ))


# Page geometry — match the existing risk-summary PDF so binders feel
# like part of the same product family.
_PAGE_WIDTH = 7.1 * inch  # usable width after letter margins


# ── Small primitives ─────────────────────────────────────────────────────────


def _priority_color(priority: str):
    """Map task priority to a chip background colour.  Same palette
    used in the dashboard so the printed page reads identically."""
    return {
        "critical": C_RED,
        "high":     C_ORANGE,
        "medium":   C_ACCENT,
        "low":      C_GRAY,
    }.get(priority, C_GRAY)


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _date(iso: Optional[str]) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD`` for the binder.  None
    becomes em-dash so empty cells still align in fixed-width tables."""
    if not iso:
        return "—"
    return iso[:10]


def _datetime(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    # Strip subseconds / timezone for printable tightness.
    return iso[:16].replace("T", " ")


# ── Cover page ───────────────────────────────────────────────────────────────


def _render_cover(story, styles, binder: DOTBinder) -> None:
    _add_header(
        story, styles,
        title="DOT Maintenance Compliance Binder",
        subtitle=binder.account_name or f"Account #{binder.account_id}",
        date_str=f"Generated {binder.generated_at} by {binder.generated_by_name}",
    )

    story.append(Paragraph(
        f"Coverage period: <b>{binder.coverage_start}</b> through "
        f"<b>{binder.coverage_end}</b>",
        styles["NormalBody"],
    ))
    story.append(Spacer(1, 12))

    # Fleet-wide stat cards.  Two rows of four so the summary fits on
    # the cover page without crowding the title block.
    s = binder.summary
    row1 = [[
        _mini_stat(styles, str(s.total_vehicles),       "Vehicles",      C_ACCENT),
        _mini_stat(styles, str(s.completed_services),   "Services Done", C_GREEN),
        _mini_stat(styles, str(s.open_tasks),           "Open Tasks",    C_ACCENT),
        _mini_stat(styles, str(s.overdue_tasks),        "Overdue",       C_RED),
    ]]
    row2 = [[
        _mini_stat(styles, str(s.work_order_count),     "Work Orders",   C_ACCENT),
        _mini_stat(styles, _money(s.total_spend),       "Total Spend",   C_GREEN),
        _mini_stat(styles, str(s.unique_vendors),       "Vendors",       C_ACCENT),
        _mini_stat(styles, str(s.dot_inspections_completed), "DOT Inspections", C_GREEN),
    ]]
    for grid in (row1, row2):
        t = Table(grid, colWidths=[_PAGE_WIDTH / 4] * 4, rowHeights=[52])
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
            ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
            ("INNERGRID",    (0, 0), (-1, -1), 0.5, C_GRAY),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(t)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<b>Contents</b>: per-vehicle maintenance and work-order "
        "records covering the period above.  Original invoice PDFs and "
        "shop photographs are retained in the storage backend — listed "
        "by filename within each work order; contact the account "
        "administrator for the original files.",
        styles["NormalBody"],
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<i>Records retained per 49 CFR § 396 — Motor Carrier "
        "Inspection, Repair, and Maintenance.</i>",
        styles["NormalSmall"],
    ))


# ── Vehicle section ──────────────────────────────────────────────────────────


def _render_vehicle(story, styles, vehicle: BinderVehicle) -> None:
    # Vehicle header banner.  Bigger than the inner section titles so
    # an inspector flipping past truck pages can spot the boundary at
    # a glance.
    header_text = (
        f"<b>VEHICLE #{vehicle.vehicle_name}</b>"
        + (f" &nbsp;·&nbsp; <font color='{C_GRAY.hexval()}'>{vehicle.company_code}</font>"
           if vehicle.company_code else "")
    )
    odo_text = (
        f"Current odometer: <b>{vehicle.odometer_mi:,.0f} mi</b>"
        if vehicle.odometer_mi is not None
        else "Current odometer: not reported"
    )

    header_table = Table(
        [[Paragraph(header_text, styles["SectionTitle"])],
         [Paragraph(odo_text, styles["NormalBody"])]],
        colWidths=[_PAGE_WIDTH],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING",   (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # The truck's papers — before everything else, because this is the
    # first thing asked for at the roadside and in an audit, and the
    # binder could not show them at all until now.
    if vehicle.documents:
        expired = sum(1 for d in vehicle.documents if d.expired)
        flag = (f"  &nbsp;<font color='{C_RED.hexval()}'>"
                f"({expired} EXPIRED)</font>" if expired else "")
        story.append(Paragraph(
            f"DOCUMENTS ON FILE{flag}", styles["SectionSubtitle"]))
        _render_document_table(story, styles, vehicle.documents)
        story.append(Spacer(1, 10))

    # DOT inspections — surface first because regulators always look
    # for these.  When the truck has none, we skip the section entirely
    # rather than rendering an empty placeholder.
    if vehicle.dot_inspections:
        story.append(Paragraph("ANNUAL DOT INSPECTIONS", styles["SectionSubtitle"]))
        _render_task_table(story, styles, vehicle.dot_inspections, show_completion=True)
        story.append(Spacer(1, 10))

    # Open maintenance — only if there's something to show.  Lists
    # everything actionable so the inspector sees the forward schedule
    # (not just past compliance).
    if vehicle.open_tasks:
        story.append(Paragraph(
            f"OPEN MAINTENANCE  &nbsp;<font color='{C_GRAY.hexval()}'>"
            f"({len(vehicle.open_tasks)} tasks)</font>",
            styles["SectionSubtitle"],
        ))
        _render_task_table(story, styles, vehicle.open_tasks, show_completion=False)
        story.append(Spacer(1, 10))

    # Completed services — the bulk of the audit trail.  Each row
    # carries attestation + work-order linkage when present.
    if vehicle.completed_tasks:
        story.append(Paragraph(
            f"COMPLETED SERVICES  &nbsp;<font color='{C_GRAY.hexval()}'>"
            f"({len(vehicle.completed_tasks)} services)</font>",
            styles["SectionSubtitle"],
        ))
        _render_task_table(story, styles, vehicle.completed_tasks, show_completion=True)
        story.append(Spacer(1, 10))
    else:
        story.append(Paragraph(
            "<i>No completed services within coverage window.</i>",
            styles["NormalSmall"],
        ))
        story.append(Spacer(1, 8))

    # Work orders — vendor + total + invoice.  Parts roll up inside
    # each row so a single line shows the full receipt.
    if vehicle.work_orders:
        story.append(Paragraph(
            f"WORK ORDERS  &nbsp;<font color='{C_GRAY.hexval()}'>"
            f"({len(vehicle.work_orders)} records)</font>",
            styles["SectionSubtitle"],
        ))
        _render_work_order_table(story, styles, vehicle.work_orders)
        story.append(Spacer(1, 6))
        subtotal = sum(w.total_cost for w in vehicle.work_orders)
        story.append(Paragraph(
            f"<b>Vehicle subtotal: {_money(subtotal)}</b>",
            styles["NormalBody"],
        ))
        story.append(Spacer(1, 10))


def _render_document_table(story, styles, docs) -> None:
    """The papers this truck carries, as a regulator reads them.

    An EXPIRED row says so in words and in red, rather than printing a
    date and leaving the reader to do the arithmetic — the whole
    question being asked is whether the certificate was valid, and a
    binder that makes an inspector work it out has answered nothing.
    """
    rows = [[
        Paragraph("<b>Document</b>", styles["NormalSmall"]),
        Paragraph("<b>File</b>", styles["NormalSmall"]),
        Paragraph("<b>Issued</b>", styles["NormalSmall"]),
        Paragraph("<b>Expires</b>", styles["NormalSmall"]),
    ]]
    for d in docs:
        label = (d.doc_type or "").replace("_", " ").title()
        if d.expired:
            expires = (f"<font color='{C_RED.hexval()}'><b>EXPIRED "
                       f"{d.expires_at}</b></font>")
        elif d.expires_at:
            expires = d.expires_at
        else:
            # No date is not a failing: a title never expires.  Say so
            # rather than leaving a blank cell to read as an omission.
            expires = f"<font color='{C_GRAY.hexval()}'>no expiry</font>"
        rows.append([
            Paragraph(label or "—", styles["NormalSmall"]),
            Paragraph(_clamp(d.file_name or "—", 60), styles["NormalSmall"]),
            Paragraph(d.issued_at or "—", styles["NormalSmall"]),
            Paragraph(expires, styles["NormalSmall"]),
        ])

    tbl = Table(rows, colWidths=[
        1.60 * inch,   # document
        2.90 * inch,   # file
        1.20 * inch,   # issued
        1.40 * inch,   # expires
    ])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",    (0, 0), (-1, 0), C_WHITE),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.25, C_GRAY),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT_BG]),
    ]))
    story.append(tbl)


def _render_task_table(
    story, styles, tasks: list[BinderTask],
    *, show_completion: bool,
) -> None:
    """Render a tasks block as a compact tabular row set.

    ``show_completion`` controls whether the rightmost column shows
    completion date + attester (for closed tasks) OR due date +
    priority (for open tasks).  Same column count either way so the
    column widths stay consistent down the page.
    """
    rows = [[
        Paragraph("<b>#</b>", styles["NormalSmall"]),
        Paragraph("<b>Type</b>", styles["NormalSmall"]),
        Paragraph("<b>Description</b>", styles["NormalSmall"]),
        Paragraph(
            "<b>Completed</b>" if show_completion else "<b>Due</b>",
            styles["NormalSmall"],
        ),
        Paragraph(
            "<b>Attester / WO</b>" if show_completion else "<b>Priority</b>",
            styles["NormalSmall"],
        ),
    ]]

    for t in tasks:
        rows.append([
            Paragraph(f"#{t.id}", styles["NormalSmall"]),
            Paragraph(_task_type_label(t.task_type), styles["NormalSmall"]),
            Paragraph(_clamp(t.description or "—", 90), styles["NormalSmall"]),
            Paragraph(
                _completion_cell(t) if show_completion else _due_cell(t),
                styles["NormalSmall"],
            ),
            Paragraph(
                _attestation_cell(t) if show_completion else _priority_cell(t),
                styles["NormalSmall"],
            ),
        ])

    tbl = Table(rows, colWidths=[
        0.45 * inch,   # #
        1.30 * inch,   # type
        2.50 * inch,   # description
        1.30 * inch,   # date
        1.55 * inch,   # attester / priority
    ])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",    (0, 0), (-1, 0), C_WHITE),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.25, C_GRAY),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Zebra striping for data rows only (header already dark).
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT_BG]),
    ]))
    story.append(KeepTogether(tbl))


def _render_work_order_table(
    story, styles, work_orders: list[BinderWorkOrder],
) -> None:
    rows = [[
        Paragraph("<b>WO #</b>", styles["NormalSmall"]),
        Paragraph("<b>Date</b>", styles["NormalSmall"]),
        Paragraph("<b>Vendor</b>", styles["NormalSmall"]),
        Paragraph("<b>Invoice</b>", styles["NormalSmall"]),
        Paragraph("<b>Parts</b>", styles["NormalSmall"]),
        Paragraph("<b>Total</b>", styles["NormalSmall"]),
        Paragraph("<b>Pmt</b>", styles["NormalSmall"]),
    ]]

    for w in work_orders:
        # Compact parts cell: "Oil filter ×1, Engine oil ×4"
        if w.parts:
            parts_text = ", ".join(
                f"{p.name} ×{p.quantity:g}" for p in w.parts[:4]
            )
            if len(w.parts) > 4:
                parts_text += f" + {len(w.parts) - 4} more"
        else:
            parts_text = "—"
        attach_note = (
            f" <font color='{C_GRAY.hexval()}'>({w.attachment_count} file{'s' if w.attachment_count != 1 else ''})</font>"
            if w.attachment_count else ""
        )
        rows.append([
            Paragraph(f"#{w.id}", styles["NormalSmall"]),
            Paragraph(_date(w.service_date), styles["NormalSmall"]),
            Paragraph(_clamp(w.vendor_name or "—", 32), styles["NormalSmall"]),
            Paragraph(
                f"<font face='Courier'>{w.invoice_number}</font>{attach_note}"
                if w.invoice_number else attach_note or "—",
                styles["NormalSmall"],
            ),
            Paragraph(_clamp(parts_text, 60), styles["NormalSmall"]),
            Paragraph(_money(w.total_cost), styles["NormalSmall"]),
            Paragraph(_pmt_chip(w.payment_status), styles["NormalSmall"]),
        ])

    tbl = Table(rows, colWidths=[
        0.55 * inch,   # WO #
        0.85 * inch,   # date
        1.40 * inch,   # vendor
        1.30 * inch,   # invoice
        1.60 * inch,   # parts
        0.85 * inch,   # total
        0.55 * inch,   # pmt
    ])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",    (0, 0), (-1, 0), C_WHITE),
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.25, C_GRAY),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHT_BG]),
        ("ALIGN",        (5, 1), (5, -1), "RIGHT"),  # money column right-aligned
    ]))
    story.append(KeepTogether(tbl))


# ── Cell renderers ───────────────────────────────────────────────────────────


def _task_type_label(t: str) -> str:
    return {
        "oil":            "Oil Change",
        "tires":          "Tire Service",
        "brakes":         "Brakes",
        "inspection":     "Inspection",
        "transmission":   "Transmission",
        "electrical":     "Electrical",
        "dot_inspection": "DOT Inspection",
        "dpf_regen":      "DPF Regen",
        "def_refill":     "DEF Refill",
        "custom":         "Custom",
    }.get(t, t.replace("_", " ").title())


def _due_cell(t: BinderTask) -> str:
    """Build the "Due" cell for an open task.  Combines date + miles +
    engine_hours into a single multi-line string so all three triggers
    are visible without a fourth column."""
    bits: list[str] = []
    if t.due_date:
        bits.append(_date(t.due_date))
    if t.due_miles is not None:
        bits.append(f"{t.due_miles:,.0f} mi")
    if t.due_engine_hours is not None:
        bits.append(f"{t.due_engine_hours:,.0f} hr")
    return "<br/>".join(bits) if bits else "—"


def _completion_cell(t: BinderTask) -> str:
    out = _date(t.completed_at)
    if t.last_odometer is not None:
        out += f"<br/>{t.last_odometer:,.0f} mi"
    return out


def _attestation_cell(t: BinderTask) -> str:
    """Combine attestation + work-order link into one column so the
    table stays 5-wide.  Inspector cares about both: who confirmed it
    AND which invoice covers it."""
    parts: list[str] = []
    if t.attested_by_name:
        parts.append(f"✓ {t.attested_by_name}")
    if t.work_order_id:
        parts.append(f"WO #{t.work_order_id}")
    return "<br/>".join(parts) if parts else "—"


def _priority_cell(t: BinderTask) -> str:
    """Coloured chip text for an open task.  Uses inline HTML colour
    rather than a separate Table cell with BACKGROUND so the priority
    travels with the row when the table flows across pages."""
    col = _priority_color(t.priority)
    label = t.priority.upper()
    return f"<b><font color='{col.hexval()}'>{label}</font></b>"


def _pmt_chip(status: str) -> str:
    col = {
        "paid":    C_GREEN,
        "partial": C_YELLOW,
        "unpaid":  C_ORANGE,
        "void":    C_GRAY,
    }.get(status, C_GRAY)
    return f"<b><font color='{col.hexval()}'>{status[:4].upper() if status else '—'}</font></b>"


def _clamp(s: str, max_len: int) -> str:
    """Trim long strings with an ellipsis so table cells stay one line.
    HTML-escape ampersand so the snippet renders correctly through
    reportlab's mini-markup."""
    s = (s or "").replace("&", "&amp;")
    if len(s) > max_len:
        return s[:max_len - 1] + "…"
    return s


# ── Closing page ─────────────────────────────────────────────────────────────


def _render_signature_page(story, styles, binder: DOTBinder) -> None:
    s = binder.summary
    story.append(Paragraph("SUMMARY & CERTIFICATION", styles["SectionTitle"]))
    story.append(Spacer(1, 12))

    sum_rows = [
        ["Total vehicles audited",      str(s.total_vehicles)],
        ["Completed services",          str(s.completed_services)],
        ["Open maintenance tasks",      str(s.open_tasks)],
        ["Overdue (at report date)",    str(s.overdue_tasks)],
        ["Work orders recorded",        str(s.work_order_count)],
        ["Total spend",                 _money(s.total_spend)],
        ["Unique vendors",              str(s.unique_vendors)],
        ["DOT annual inspections",      str(s.dot_inspections_completed)],
        ["Documents on file",            str(s.documents_on_file)],
        ["  of which expired",           str(s.documents_expired)],
    ]
    tbl = Table(sum_rows, colWidths=[3.0 * inch, 3.0 * inch])
    tbl.setStyle(TableStyle([
        ("BOX",          (0, 0), (-1, -1), 0.5, C_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.25, C_GRAY),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND",   (0, 0), (0, -1), C_LIGHT_BG),
        ("FONTNAME",     (1, 0), (1, -1), "Helvetica-Bold"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 24))

    # Signature block — left blank, intended to be filled in by hand
    # or counter-signed digitally before the binder is handed over.
    story.append(Paragraph(
        "I certify that the maintenance records above are accurate "
        "and complete to the best of my knowledge.",
        styles["NormalBody"],
    ))
    story.append(Spacer(1, 30))
    sig_rows = [
        ["Signature: _______________________________",
         "Date: __________________"],
        ["Printed name: ____________________________",
         "Title: __________________"],
    ]
    sig_tbl = Table(sig_rows, colWidths=[3.7 * inch, 2.4 * inch])
    sig_tbl.setStyle(TableStyle([
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("FONTSIZE",     (0, 0), (-1, -1), 10),
    ]))
    story.append(sig_tbl)


# ── Public API ───────────────────────────────────────────────────────────────


def render_dot_binder_pdf(binder: DOTBinder) -> bytes:
    """Render a DOT compliance binder as PDF bytes.

    Caller decides what to do with the bytes — stream to HTTP client,
    save to disk, email, etc.  In-memory buffer keeps the renderer
    side-effect-free.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.5 * inch, bottomMargin=0.6 * inch,
        title=f"DOT Binder — {binder.account_name}",
        author=binder.generated_by_name,
    )
    styles = _build_styles()
    _add_binder_styles(styles)
    story: list = []

    _render_cover(story, styles, binder)

    if not binder.vehicles:
        # Empty fleet — render a placeholder so the file isn't blank.
        story.append(PageBreak())
        story.append(Paragraph("No vehicles found for this account.", styles["NormalBody"]))
    else:
        for v in binder.vehicles:
            story.append(PageBreak())
            _render_vehicle(story, styles, v)

    story.append(PageBreak())
    _render_signature_page(story, styles, binder)

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
