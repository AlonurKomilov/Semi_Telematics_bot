"""Smoke tests for the PTI inspection PDF report generator.

Pure / no DB — verifies the builder emits a valid PDF across the
shapes it must handle (defects, photos, documents, signatures, and
the minimal/empty case).
"""

from __future__ import annotations

import io
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from capabilities.pti import pdf as pti_pdf

_PNG_SIG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)


def _jpeg_bytes() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (320, 240), (180, 120, 120)).save(b, "JPEG")
    return b.getvalue()


def _full_detail() -> dict:
    return {
        "id": 7, "vehicle_name": "107", "inspection_type": "weekly", "user_id": 5,
        "status": "reviewed", "review_status": "needs_service",
        "submitted_at": "2026-05-27T14:30:00+00:00",
        "reviewed_at": "2026-05-27T15:00:00+00:00",
        "defects_count": 2, "has_oos_defect": 1,
        "location_lat": 40.7128, "location_lon": -74.0060, "location_source": "vehicle",
        "driver_name": "John Driver", "reviewed_by_name": "Jane Fleet",
        "driver_signature": _PNG_SIG, "driver_signed_at": "2026-05-27T14:29:00+00:00",
        "reviewer_signature": _PNG_SIG, "reviewer_signed_at": "2026-05-27T15:00:00+00:00",
        "review_notes": "Replace left mudflap.",
        "items": [
            {"id": 1, "label": "Brakes", "category": "brakes", "status": "ok", "sort_order": 1, "item_type": "check"},
            {"id": 3, "label": "Mudflap", "category": "exterior", "status": "oos", "sort_order": 3, "item_type": "photo"},
            {"id": 4, "label": "Annual report", "category": "docs", "status": "pending", "sort_order": 4, "item_type": "document"},
        ],
        "media": [
            {"id": 100, "item_id": 3, "media_type": "photo", "content_type": "image/jpeg",
             "file_name": "mud.jpg",
             "ai_review_result": '{"verdict":"likely_defect","summary":"Torn."}'},
            {"id": 101, "item_id": 4, "media_type": "document", "content_type": "application/pdf",
             "file_name": "annual.pdf"},
        ],
    }


class TestInspectionPdf:
    def test_full_report_is_valid_pdf(self):
        out = pti_pdf.build_inspection_pdf(_full_detail(), {100: _jpeg_bytes()})
        assert out[:5] == b"%PDF-"
        assert len(out) > 1500

    def test_missing_photo_bytes_still_builds(self):
        # AI fetch failed (e.g. expired Drive token) → no image bytes.
        out = pti_pdf.build_inspection_pdf(_full_detail(), {})
        assert out[:5] == b"%PDF-"

    def test_minimal_unsubmitted_inspection(self):
        detail = {
            "id": 1, "vehicle_name": "12", "inspection_type": "daily_pre_trip",
            "user_id": 9, "status": "in_progress",
            "items": [{"id": 1, "label": "Horn", "category": "cab", "status": "ok", "sort_order": 1}],
            "media": [],
        }
        out = pti_pdf.build_inspection_pdf(detail, {})
        assert out[:5] == b"%PDF-"

    def test_strongest_verdict_picks_most_severe(self):
        media = [
            {"ai_review_result": '{"verdict":"ok","summary":"fine"}'},
            {"ai_review_result": '{"verdict":"likely_defect","summary":"bad"}'},
            {"ai_review_result": '{"verdict":"possible_issue","summary":"maybe"}'},
        ]
        v = pti_pdf._strongest_verdict(media)
        assert v["verdict"] == "likely_defect"

    def test_strongest_verdict_none_when_no_ai(self):
        assert pti_pdf._strongest_verdict([{"media_type": "photo"}]) is None
