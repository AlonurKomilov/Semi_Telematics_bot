"""Reading the expiry off the paper instead of retyping it.

The expiry date is what the alert, the tones and the Expiring/Expired
tabs all read, and it used to depend on someone transcribing a cab card
correctly.  These pin the rule that makes a suggestion safe: every
field degrades to EMPTY rather than to a guess.  An unusable suggestion
costs one keystroke; a plausible wrong one costs a missed expiry, and
a pre-filled field gives the operator no reason to doubt it.
"""
from __future__ import annotations

from datetime import date

import pytest

from features.vehicles.documents.extraction import (
    EXTRACT_MIMES, _parse_model_json, coerce_extract,
)


def test_it_reads_the_fields_a_cab_card_carries():
    out = coerce_extract({
        "doc_type": "Cab Card", "issued_at": "2026-01-01",
        "expires_at": "2027-01-31", "unit_hint": "110",
        "plate_hint": "ABC1234", "vin_hint": "1HGTEST0000000001",
        "confidence": {"doc_type": 1.0, "dates": 0.9},
        "notes": "Clean scan.",
    })
    assert out["doc_type"] == "cab_card"      # normalised to the wire key
    assert out["expires_at"] == "2027-01-31"
    assert out["unit_hint"] == "110"
    assert out["confidence"]["dates"] == 0.9


def test_a_date_it_could_not_read_stays_empty():
    """US-format, prose, garbage — all mean "ask the human", never a
    date the form would present as fact."""
    for bad in ("01/31/2027", "Jan 2027", "expires soon", "", None, "2027-13-45"):
        assert coerce_extract({"expires_at": bad})["expires_at"] == ""


def test_a_date_outside_a_sane_window_is_a_misread():
    """A registration does not expire in 1971 or in 2199.  Both extremes
    break the feature quietly — a far past fires the alert instantly, a
    far future silences it forever."""
    assert coerce_extract({"expires_at": "1971-05-01"})["expires_at"] == ""
    assert coerce_extract({"expires_at": "2199-05-01"})["expires_at"] == ""
    ok = f"{date.today().year + 2}-05-01"
    assert coerce_extract({"expires_at": ok})["expires_at"] == ok


def test_an_invented_document_type_is_refused():
    """The model may only choose from the vocabulary the server
    validates against, or the upload would 422 after a confident
    pre-fill."""
    assert coerce_extract({"doc_type": "smog_thing"})["doc_type"] == ""
    assert coerce_extract({"doc_type": "annual inspection"})["doc_type"] == (
        "annual_inspection")


def test_dates_read_off_the_wrong_lines_drop_the_lesser_one():
    """Issued after expired means one of them came off the wrong row.
    The expiry is the field that matters, so it survives and the
    doubtful one goes blank."""
    out = coerce_extract({"issued_at": "2028-01-01", "expires_at": "2027-01-01"})
    assert out["expires_at"] == "2027-01-01"
    assert out["issued_at"] == ""


def test_a_fenced_or_chatty_reply_still_parses():
    assert _parse_model_json('```json\n{"expires_at": "2027-01-31"}\n```') == {
        "expires_at": "2027-01-31"}
    assert _parse_model_json('Sure!\n{"expires_at": "2027-01-31"}') == {
        "expires_at": "2027-01-31"}
    assert _parse_model_json("no json here") is None
    assert _parse_model_json("[1,2]") is None


def test_it_accepts_the_same_files_the_invoice_scanner_does():
    """A cab card photographed on a phone is the same artifact as an
    invoice photographed on a phone.  Imported HERE rather than in
    production code — the coupling costs nothing in a test and would
    tie two features together anywhere else."""
    from features.work_orders.extraction import EXTRACT_MIMES as INVOICE_MIMES

    assert EXTRACT_MIMES == INVOICE_MIMES


@pytest.mark.asyncio
async def test_an_unreachable_model_says_so_instead_of_guessing(monkeypatch):
    from features.vehicles.documents import extraction as ex

    async def _dead(*a, **kw):
        return "", None
    monkeypatch.setattr("capabilities.ai.vision.generate_with_file", _dead)

    out = await ex.extract_document(b"%PDF-1.4", "application/pdf",
                                    account_id=1)
    assert out["ok"] is False and "unavailable" in out["error"]
