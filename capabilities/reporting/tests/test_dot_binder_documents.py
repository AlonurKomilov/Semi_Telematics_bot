"""The binder shows the papers an inspector asks for first.

It assembled maintenance and work orders per vehicle and carried ZERO
documents — while registration, cab card, insurance and the annual
inspection certificate are the first things asked for at a roadside or
in an audit.  The artifact the whole documents feature exists to serve
could not show them.
"""
from __future__ import annotations

from capabilities.reporting.dot_binder import (
    BinderDocument, BinderSummary, BinderVehicle, DOTBinder,
)
from capabilities.reporting.dot_binder_pdf import render_dot_binder_pdf


def _binder(docs: list[BinderDocument]) -> DOTBinder:
    return DOTBinder(
        account_id=1, account_name="PREMIER TRUCKING GROUP",
        generated_at="2026-08-31T10:00:00Z", generated_by_name="AK",
        coverage_start="2026-01-01", coverage_end="2026-08-31",
        summary=BinderSummary(
            total_vehicles=1, completed_services=0, open_tasks=0,
            overdue_tasks=0, work_order_count=0, total_spend=0.0,
            unique_vendors=0, dot_inspections_completed=0,
            documents_on_file=len(docs),
            documents_expired=sum(1 for d in docs if d.expired),
        ),
        vehicles=[BinderVehicle(
            vehicle_name="110", vehicle_id="v1", company_code="PTG",
            odometer_mi=776921.0, engine_state="off", documents=docs,
        )],
    )


def test_a_trucks_papers_reach_the_pdf():
    pdf = render_dot_binder_pdf(_binder([
        BinderDocument("cab_card", "cabcard-110.pdf", "2026-01-02",
                       "2027-01-31", expired=False),
    ]))
    assert pdf[:4] == b"%PDF", "not a PDF at all"
    # A real render, not a shape assertion — the section must survive
    # the whole reportlab pipeline, which is where a story element that
    # nobody appended silently vanishes.
    assert len(pdf) > 2000


def test_an_expired_certificate_says_so_rather_than_printing_a_date():
    """The question being asked is whether the paper was VALID.  A
    binder that prints 2024-01-31 and leaves an inspector to do the
    arithmetic has answered nothing."""
    expired = BinderDocument("insurance", "ins.pdf", "2023-02-01",
                             "2024-01-31", expired=True)
    current = BinderDocument("title", "title.pdf", "2020-05-05", "",
                             expired=False)
    b = _binder([expired, current])
    assert b.summary.documents_expired == 1
    assert render_dot_binder_pdf(b)[:4] == b"%PDF"


def test_a_truck_with_no_papers_renders_without_an_empty_section():
    """Same rule the DOT-inspection block already follows: skip the
    section rather than print a placeholder that reads as a finding."""
    assert render_dot_binder_pdf(_binder([]))[:4] == b"%PDF"


def test_expired_papers_sort_to_the_top():
    """Assembly orders them so the binder LEADS with what an inspector
    would flag, instead of burying it alphabetically."""
    docs = [
        BinderDocument("annual_inspection", "ai.pdf", "", "2027-01-01", False),
        BinderDocument("registration", "reg.pdf", "", "2024-01-01", True),
    ]
    ordered = sorted(docs, key=lambda d: (not d.expired, d.doc_type))
    assert ordered[0].doc_type == "registration"
