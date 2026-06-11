"""Single-file PDF report for a completed PTI inspection.

Produces one self-contained PDF per inspection — the compliance
artifact a fleet hands to a DOT auditor, insurer, or files in the
carrier record.  Bundles into ONE document:

  * Header — vehicle, type, driver, submit/review timestamps, outcome
  * Summary band — defects, OOS flag, item count, GPS fix
  * Per-item rows — status (OK/Minor/Major/OOS/N/A), driver notes, the
    AI vision verdict, and the captured photos embedded inline
  * Document slots (annual report, registration) — images embedded,
    PDFs listed by name
  * Signatures — driver + optional fleet reviewer, rendered from the
    captured PNG

Pure / synchronous: the route gathers all photo bytes (network I/O)
first and passes them in via ``images``; this module only does CPU
work so the caller can run it in a thread without holding the event
loop on Drive fetches.
"""

from __future__ import annotations

import io
import logging

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, HRFlowable, Image as RLImage,
)

from capabilities.reporting.pdf_base import (
    _build_styles, _add_header, _make_section_header_table,
    _fmt_time, _safe,
    C_ACCENT, C_GRAY, C_LIGHT_BG,
)

logger = logging.getLogger(__name__)

_PAGE_W = 7.1 * inch

# Item status → (label, hex colour) matching the dashboard grammar.
_ITEM_STATUS = {
    "ok":      ("OK",       "#22c55e"),
    "minor":   ("Minor",    "#eab308"),
    "major":   ("Major",    "#f59e0b"),
    "oos":     ("OOS",      "#e94560"),
    "na":      ("N/A",      "#64748b"),
    "pending": ("Pending",  "#64748b"),
}

_REVIEW_STATUS = {
    "approved":          ("Approved",          "#22c55e"),
    "needs_service":     ("Needs Service",     "#f59e0b"),
    "rejected":          ("Rejected",          "#e94560"),
    "revision_required": ("Revision Required", "#eab308"),
}

_AI_VERDICT = {
    "ok":             ("AI: OK",              "#22c55e"),
    "possible_issue": ("AI: possible issue",  "#f59e0b"),
    "likely_defect":  ("AI: likely defect",   "#e94560"),
    "unclear":        ("AI: unclear",         "#64748b"),
}


def _photo_flowable(raw: bytes, max_w: float, max_h: float):
    """Downscale + embed an image as a reportlab flowable.

    Returns None when the bytes aren't a decodable image (e.g. a video
    or a fetch that returned junk) so the caller can render a textual
    placeholder instead.
    """
    try:
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(raw)).convert("RGB")
        # Cap the embedded resolution so a 12 MP phone photo doesn't
        # bloat the PDF — inspection photos read fine at ~1000px.
        im.thumbnail((1000, 1000))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        buf.seek(0)
        iw, ih = ImageReader(buf).getSize()
        scale = min(max_w / iw, max_h / ih, 1.0)
        return RLImage(buf, width=iw * scale, height=ih * scale)
    except Exception as e:
        logger.debug("PTI PDF: image embed failed: %s", e)
        return None


def _decode_signature(data_url: str | None) -> bytes | None:
    """Decode a ``data:image/png;base64,…`` signature into raw bytes."""
    if not data_url or not isinstance(data_url, str):
        return None
    if "," not in data_url:
        return None
    import base64
    try:
        return base64.b64decode(data_url.split(",", 1)[1], validate=False)
    except Exception:
        return None


def _item_status_chip(styles, status: str):
    label, hexc = _ITEM_STATUS.get((status or "pending").lower(), ("?", "#64748b"))
    return Paragraph(
        f'<font color="{hexc}"><b>{label}</b></font>', styles["CellBold"],
    )


def build_inspection_pdf(detail: dict, images: dict[int, bytes]) -> bytes:
    """Render a completed inspection to PDF bytes.

    Args:
        detail: the GET /inspections/{id} payload — inspection row plus
            ``items``, ``media``, ``driver_name``, ``reviewed_by_name``,
            and the signature columns.
        images: ``{media_id: raw_bytes}`` for photo / image-document
            media the caller successfully fetched from storage.  Missing
            ids render as "[photo unavailable]".
    """
    styles = _build_styles()
    story: list = []

    vehicle = _safe(detail.get("vehicle_name"), "—")
    ins_type = (detail.get("inspection_type") or "").replace("_", " ").title()
    driver = detail.get("driver_name") or f"user {detail.get('user_id', '')}"

    _add_header(
        story, styles,
        "Pre-Trip Inspection Report",
        f"Vehicle {vehicle}  ·  {ins_type}",
        f"Driver: {driver}",
    )

    # ── Summary band ────────────────────────────────────────────────
    review_status = (detail.get("review_status") or "").lower()
    outcome_label, outcome_hex = _REVIEW_STATUS.get(
        review_status, (detail.get("status", "—").replace("_", " ").title(), "#64748b"),
    )
    loc_lat, loc_lon = detail.get("location_lat"), detail.get("location_lon")
    if loc_lat is not None and loc_lon is not None:
        src = detail.get("location_source") or "—"
        gps = f"{float(loc_lat):.5f}, {float(loc_lon):.5f} ({src})"
    else:
        gps = "—"

    meta_rows = [
        [
            Paragraph("<b>Submitted</b>", styles["CellBold"]),
            Paragraph(_fmt_time(detail.get("submitted_at") or ""), styles["CellText"]),
            Paragraph("<b>Outcome</b>", styles["CellBold"]),
            Paragraph(f'<font color="{outcome_hex}"><b>{outcome_label}</b></font>', styles["CellBold"]),
        ],
        [
            Paragraph("<b>Defects</b>", styles["CellBold"]),
            Paragraph(
                f'{detail.get("defects_count", 0)}'
                + ('  <font color="#e94560"><b>· OOS</b></font>' if detail.get("has_oos_defect") else ''),
                styles["CellText"],
            ),
            Paragraph("<b>Reviewed by</b>", styles["CellBold"]),
            Paragraph(_safe(detail.get("reviewed_by_name")), styles["CellText"]),
        ],
        [
            Paragraph("<b>Location</b>", styles["CellBold"]),
            Paragraph(gps, styles["CellText"]),
            Paragraph("<b>Reviewed</b>", styles["CellBold"]),
            Paragraph(_fmt_time(detail.get("reviewed_at") or ""), styles["CellText"]),
        ],
    ]
    meta = Table(meta_rows, colWidths=[1.2 * inch, 2.35 * inch, 1.2 * inch, 2.35 * inch])
    meta.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), C_LIGHT_BG),
        ("BACKGROUND",    (2, 0), (2, -1), C_LIGHT_BG),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
        ("BOX",           (0, 0), (-1, -1), 0.6, C_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(meta)
    story.append(Spacer(1, 10))

    # ── Items ───────────────────────────────────────────────────────
    items = sorted(detail.get("items") or [], key=lambda it: it.get("sort_order", 0))
    media = detail.get("media") or []
    media_by_item: dict[int, list] = {}
    for m in media:
        iid = m.get("item_id")
        if iid is not None:
            media_by_item.setdefault(int(iid), []).append(m)

    story.append(_make_section_header_table(styles, "INSPECTION ITEMS", C_ACCENT))
    story.append(Spacer(1, 6))

    for it in items:
        block: list = []
        # Header row: label + category + status chip.
        hdr = Table(
            [[
                Paragraph(
                    f'<b>{_safe(it.get("label"))}</b>'
                    f'<font color="#94a3b8" size="7">  {_safe(it.get("category"), "")}</font>',
                    styles["CellText"],
                ),
                _item_status_chip(styles, it.get("status", "pending")),
            ]],
            colWidths=[5.9 * inch, 1.2 * inch],
        )
        hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT_BG),
            ("BOX",           (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("ALIGN",         (1, 0), (1, 0), "RIGHT"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]))
        block.append(hdr)

        if it.get("notes"):
            block.append(Spacer(1, 2))
            block.append(Paragraph(
                f'<i>Note:</i> {_safe(it.get("notes"))}', styles["CellText"],
            ))

        # AI verdict (per-photo verdicts roll up to the strongest one).
        item_media = media_by_item.get(int(it["id"]), []) if it.get("id") is not None else []
        verdict = _strongest_verdict(item_media)
        if verdict:
            vlabel, vhex = _AI_VERDICT.get(verdict["verdict"], ("AI", "#64748b"))
            summ = _safe(verdict.get("summary"), "")
            block.append(Paragraph(
                f'<font color="{vhex}"><b>{vlabel}</b></font> {summ}', styles["CellText"],
            ))

        # Photos / documents embedded inline (2 per row).
        thumbs: list = []
        for m in item_media:
            mid = int(m["id"])
            mtype = (m.get("media_type") or "photo").lower()
            ctype = (m.get("content_type") or "").lower()
            if mtype == "document" and "pdf" in ctype:
                thumbs.append(Paragraph(
                    f'\U0001F4C4 {_safe(m.get("file_name"), "document.pdf")} '
                    f'<font color="#94a3b8" size="7">(attached separately)</font>',
                    styles["CellText"],
                ))
                continue
            if mtype == "video":
                thumbs.append(Paragraph(
                    f'\U0001F3A5 {_safe(m.get("file_name"), "video")} '
                    f'<font color="#94a3b8" size="7">(video — view online)</font>',
                    styles["CellText"],
                ))
                continue
            raw = images.get(mid)
            flow = _photo_flowable(raw, 2.9 * inch, 2.4 * inch) if raw else None
            thumbs.append(flow or Paragraph(
                '<font color="#94a3b8">[photo unavailable]</font>', styles["CellText"],
            ))

        if thumbs:
            block.append(Spacer(1, 3))
            # Pair thumbnails into rows of 2.
            rows = [thumbs[i:i + 2] for i in range(0, len(thumbs), 2)]
            for r in rows:
                if len(r) == 1:
                    r.append("")
            grid = Table(rows, colWidths=[3.45 * inch, 3.45 * inch])
            grid.setStyle(TableStyle([
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 2),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
            ]))
            block.append(grid)

        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    # ── Review notes ────────────────────────────────────────────────
    if detail.get("review_notes"):
        story.append(Spacer(1, 4))
        story.append(_make_section_header_table(styles, "FLEET REVIEW NOTES", C_ACCENT))
        story.append(Spacer(1, 6))
        story.append(Paragraph(_safe(detail.get("review_notes")), styles["CellText"]))

    # ── Signatures ───────────────────────────────────────────────────
    drv_sig = _decode_signature(detail.get("driver_signature"))
    rev_sig = _decode_signature(detail.get("reviewer_signature"))
    if drv_sig or rev_sig:
        story.append(Spacer(1, 10))
        story.append(_make_section_header_table(styles, "SIGNATURES", C_ACCENT))
        story.append(Spacer(1, 6))

        def _sig_cell(label, who, when, raw):
            cell: list = [Paragraph(f"<b>{label}</b>", styles["CellBold"])]
            if who:
                cell.append(Paragraph(_safe(who), styles["CellText"]))
            flow = _photo_flowable(raw, 2.8 * inch, 1.1 * inch) if raw else None
            cell.append(flow or Paragraph('<font color="#94a3b8">—</font>', styles["CellText"]))
            if when:
                cell.append(Paragraph(
                    f'<font color="#94a3b8" size="7">{_fmt_time(when)}</font>',
                    styles["CellText"],
                ))
            return cell

        sig_row = [[
            _sig_cell("Driver", driver, detail.get("driver_signed_at"), drv_sig),
            _sig_cell("Fleet reviewer", detail.get("reviewed_by_name"),
                      detail.get("reviewer_signed_at"), rev_sig),
        ]]
        sig_tbl = Table(sig_row, colWidths=[3.45 * inch, 3.45 * inch])
        sig_tbl.setStyle(TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("INNERGRID",     (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ]))
        story.append(sig_tbl)

    # PTI-specific footer — the inspection is driver-attested, not
    # telematics-derived, so the shared "Data from Samsara API" footer
    # would be misleading on a compliance record.
    story.append(Spacer(1, 16))
    story.append(HRFlowable(
        width="100%", thickness=0.5, color=C_GRAY, spaceBefore=4, spaceAfter=8,
    ))
    story.append(Paragraph(
        "Generated by 4truck  •  Pre-Trip Inspection compliance record  "
        f"•  {_fmt_time(detail.get('submitted_at') or '')}",
        styles["FooterText"],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "This report reflects the driver's attested inspection. "
        "CONFIDENTIAL — do not distribute without authorization.",
        styles["FooterText"],
    ))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.5 * inch, bottomMargin=0.6 * inch,
        title=f"PTI Report — Vehicle {vehicle}",
    )
    doc.build(story)
    return buf.getvalue()


def _strongest_verdict(item_media: list) -> dict | None:
    """Pick the most-severe AI verdict among an item's photos."""
    import json
    rank = {"likely_defect": 3, "possible_issue": 2, "unclear": 1, "ok": 0}
    best: dict | None = None
    best_rank = -1
    for m in item_media:
        raw = m.get("ai_review_result")
        if not raw:
            continue
        try:
            v = json.loads(raw)
        except Exception:
            continue
        r = rank.get(v.get("verdict", ""), -1)
        if r > best_rank:
            best, best_rank = v, r
    return best
