"""Datatruck → Work Orders module projection.

``project_external_work_orders`` lands synced shop invoices on the same
``work_orders`` table the operator's hand-entered ones live in, tagged
``source='datatruck'`` and keyed by ``external_id``.  These pin the
contract the Work Orders page relies on: synced rows show up with their
full Datatruck detail (vendor, invoice, line items), re-syncs don't
duplicate, Datatruck-owned fields refresh while operator workflow fields
are preserved, and the partial unique index never constrains the many
manual rows against each other.
"""

from __future__ import annotations

import pytest

# Shape mirrors capabilities.integrations.datatruck.sync._norm_work_order
# (enriched from the live v2 detail serializer).
_WO_ROWS = [
    {
        "external_id": "1001", "number": "WO-1", "invoice_number": "INV-1001",
        "vehicle_unit": "T100", "vendor_name": "Roadside Repair Co",
        "vendor_address": "100 Example Rd, Springfield, IL 62701",
        "vendor_phone": "(555) 010-0100", "payment_method": "efs",
        "note": "front brake job", "opened_at": "2026-06-09T00:00:00Z",
        # Internally consistent: Σ line totals (109) + tax (15) = total (124).
        # The second line is labor-only (parts_price 0) — the case that used
        # to double-count once labor_cost was added on top of the part rows.
        "tax_amount": 15.0, "total_cost": 124.0,
        "line_items": [
            {"name": "TIRE DISPOSAL FEE", "quantity": 2.0, "unit_cost": 10.0,
             "labor_price": 0.0, "labor_hours": 1.0, "total": 20.0},
            {"name": "Labor", "quantity": 1.0, "unit_cost": 0.0,
             "labor_price": 89.0, "labor_hours": 1.0, "total": 89.0},
        ],
    },
    {
        "external_id": "1002", "number": "WO-2", "invoice_number": "INV-2002",
        "vehicle_unit": "TR200", "vendor_name": "Cross-Country Truck Service",
        "opened_at": "2026-06-11T00:00:00Z", "total_cost": 120.5,
        "line_items": [],
    },
]


@pytest.mark.asyncio
async def test_project_inserts_full_datatruck_detail(db):
    n = await db.project_external_work_orders(42, _WO_ROWS, source="datatruck")
    assert n == 2
    rows = await db.list_work_orders(42)
    assert len(rows) == 2
    by_ext = {r["external_id"]: r for r in rows}
    wo = by_ext["1001"]
    assert wo["source"] == "datatruck"
    assert wo["vehicle_name"] == "T100"
    # invoice_number maps from invoice_id, NOT the WO number.
    assert wo["invoice_number"] == "INV-1001"
    # external_number IS the WO number (the source's human reference,
    # surfaced as the Source-badge hover tip).
    assert wo["external_number"] == "WO-1"
    assert wo["vendor_name"] == "Roadside Repair Co"
    assert wo["vendor_address"] == "100 Example Rd, Springfield, IL 62701"
    assert wo["vendor_phone"] == "(555) 010-0100"
    assert wo["payment_method"] == "efs"
    assert wo["notes"] == "front brake job"
    # REAL columns (float4) → compare with tolerance.
    assert wo["tax_amount"] == pytest.approx(15.0, rel=1e-5)
    assert wo["total_cost"] == pytest.approx(124.0, rel=1e-5)
    # Header roll-up: part rows carry the full per-line totals, so
    # parts_cost = Σ(line totals) and labor_cost stays 0 to avoid the
    # dashboard double-counting labor (see test_synced_total_no_double_count).
    assert wo["parts_cost"] == pytest.approx(109.0)
    assert wo["labor_cost"] == pytest.approx(0.0)
    # Real upstream invoice, not a local draft.
    assert wo["status"] == "completed"


@pytest.mark.asyncio
async def test_synced_total_no_double_count(db):
    """The dashboard recomputes Total = labor_cost + Σ(part rows) + tax.
    That must equal Datatruck's own total_price — i.e. labor lines are
    NOT counted twice (the bug that showed $1,208.87 for a $609 WO)."""
    await db.project_external_work_orders(42, [_WO_ROWS[0]], source="datatruck")
    wo = (await db.list_work_orders(42))[0]
    parts = await db.list_work_order_parts(wo["id"])
    dashboard_total = (
        wo["labor_cost"] + sum(p["total_cost"] for p in parts) + wo["tax_amount"]
    )
    assert dashboard_total == pytest.approx(wo["total_cost"], rel=1e-5)
    assert wo["labor_cost"] == 0.0


@pytest.mark.asyncio
async def test_project_creates_line_item_parts(db):
    await db.project_external_work_orders(42, _WO_ROWS, source="datatruck")
    wo = {r["external_id"]: r for r in await db.list_work_orders(42)}["1001"]
    parts = await db.list_work_order_parts(wo["id"])
    assert len(parts) == 2
    disposal = next(p for p in parts if p["part_name"] == "TIRE DISPOSAL FEE")
    assert disposal["quantity"] == 2.0
    assert disposal["unit_cost"] == 10.0
    assert disposal["total_cost"] == 20.0


@pytest.mark.asyncio
async def test_project_is_idempotent_and_refreshes(db):
    await db.project_external_work_orders(42, _WO_ROWS, source="datatruck")
    updated = [
        {**_WO_ROWS[0], "total_cost": 999.0, "vehicle_unit": "T100A",
         "vendor_name": "New Vendor"},
        _WO_ROWS[1],
    ]
    n = await db.project_external_work_orders(42, updated, source="datatruck")
    assert n == 2
    rows = await db.list_work_orders(42)
    assert len(rows) == 2  # refreshed in place, not duplicated
    wo = {r["external_id"]: r for r in rows}["1001"]
    assert wo["total_cost"] == 999.0
    assert wo["vehicle_name"] == "T100A"
    assert wo["vendor_name"] == "New Vendor"
    # Parts are seeded once — a re-sync must not duplicate them.
    parts = await db.list_work_order_parts(wo["id"])
    assert len(parts) == 2


@pytest.mark.asyncio
async def test_project_preserves_operator_workflow_fields(db):
    await db.project_external_work_orders(42, [_WO_ROWS[0]], source="datatruck")
    row = (await db.list_work_orders(42))[0]
    # Operator marks it paid and edits the note.  (Status is
    # upstream-authoritative for synced WOs — see the ratchet test.)
    await db.update_work_order(
        row["id"], 42, payment_status="paid", notes="reconciled in books",
    )
    # A re-sync refreshes Datatruck fields but keeps the operator's
    # money state (ratchets forward only) and notes (never touched).
    await db.project_external_work_orders(42, [_WO_ROWS[0]], source="datatruck")
    refreshed = (await db.list_work_orders(42))[0]
    assert refreshed["payment_status"] == "paid"
    assert refreshed["notes"] == "reconciled in books"


@pytest.mark.asyncio
async def test_project_skips_rows_without_external_id(db):
    n = await db.project_external_work_orders(
        42, [{"invoice_number": "no-id", "vehicle_unit": "X"}],
        source="datatruck",
    )
    assert n == 0
    assert await db.list_work_orders(42) == []


@pytest.mark.asyncio
async def test_manual_rows_unconstrained_by_external_index(db):
    # Two manual rows (external_id='') must coexist — the partial unique
    # index applies only to non-empty external_id.
    await db.add_work_order(42, "", "221", "Vendor A")
    await db.add_work_order(42, "", "305", "Vendor B")
    rows = await db.list_work_orders(42)
    assert len(rows) == 2
    assert all((r.get("source") or "manual") == "manual" for r in rows)


@pytest.mark.asyncio
async def test_project_resolves_plate_to_unit_and_vehicle_type(db):
    # The list-shape sync hands us the asset by PLATE; the synced fleet
    # tables carry the plate→unit mapping.  The page must show the canonical
    # UNIT number (not the plate) and learn truck-vs-trailer.
    await db.upsert_datatruck_trucks(42, [
        {"external_id": "tk-1", "unit_number": "T100", "plate_number": "ABC123"},
    ])
    await db.upsert_datatruck_trailers(42, [
        {"external_id": "tr-1", "unit_number": "TR200", "plate_number": "XYZ789"},
    ])
    rows = [
        {"external_id": "5001", "vehicle_unit": "ABC123", "vendor_name": "V",
         "total_cost": 10.0, "line_items": []},
        {"external_id": "5002", "vehicle_unit": "XYZ789", "vendor_name": "V",
         "total_cost": 10.0, "line_items": []},
    ]
    await db.project_external_work_orders(42, rows, source="datatruck")
    by_ext = {r["external_id"]: r for r in await db.list_work_orders(42)}
    assert by_ext["5001"]["vehicle_name"] == "T100"      # plate → unit
    assert by_ext["5001"]["vehicle_type"] == "truck"
    assert by_ext["5002"]["vehicle_name"] == "TR200"
    assert by_ext["5002"]["vehicle_type"] == "trailer"


@pytest.mark.asyncio
async def test_project_keeps_explicit_vehicle_type_and_unresolved_unit(db):
    # No synced fleet → the raw value is kept (graceful fallback), and an
    # explicit vehicle_type from the detail shape wins.
    rows = [{"external_id": "6001", "vehicle_unit": "T999",
             "vehicle_type": "truck", "vendor_name": "V",
             "total_cost": 10.0, "line_items": []}]
    await db.project_external_work_orders(42, rows, source="datatruck")
    wo = (await db.list_work_orders(42))[0]
    assert wo["vehicle_name"] == "T999"
    assert wo["vehicle_type"] == "truck"


@pytest.mark.asyncio
async def test_project_derives_payment_status_from_balance(db):
    """payment_type/invoice live only in the v2 detail (token can't reach
    it), so payment status is seeded from the openapi balance/paid_amount:
    cleared balance → paid, outstanding → unpaid."""
    paid = {**_WO_ROWS[0], "external_id": "2001",
            "balance": 0.0, "paid_amount": 124.0}
    unpaid = {**_WO_ROWS[0], "external_id": "2002",
              "balance": 124.0, "paid_amount": 0.0}
    await db.project_external_work_orders(42, [paid, unpaid], source="datatruck")
    rows = {r["external_id"]: r for r in await db.list_work_orders(42)}
    assert rows["2001"]["payment_status"] == "paid"
    assert rows["2002"]["payment_status"] == "unpaid"


@pytest.mark.asyncio
async def test_project_is_tenant_scoped(db):
    await db.project_external_work_orders(42, [_WO_ROWS[0]], source="datatruck")
    # Same external id, different account → a separate row, not a clash.
    await db.project_external_work_orders(99, [_WO_ROWS[0]], source="datatruck")
    assert len(await db.list_work_orders(42)) == 1
    assert len(await db.list_work_orders(99)) == 1


@pytest.mark.asyncio
async def test_plan_work_orders_new_vs_update(db):
    # 1001 already synced → update; a fresh id → new.  Read-only: no write.
    await db.project_external_work_orders(42, [_WO_ROWS[0]], source="datatruck")
    rows = [
        {"external_id": "1001", "number": "WO-1", "vendor_name": "V",
         "vehicle_unit": "T100", "total_cost": 10.0},
        {"external_id": "9999", "number": "WO-9", "vendor_name": "V2",
         "vehicle_unit": "T200", "total_cost": 20.0},
    ]
    before = len(await db.list_work_orders(42))
    diff = await db.plan_external_work_orders(42, rows)
    assert diff["counts"]["new"] == 1
    assert diff["counts"]["update"] == 1
    assert diff["new"][0]["external_id"] == "9999"
    # The update row carries a before → after diff for the changed fields.
    upd = diff["update"][0]
    changed = {c["field"]: (c["from"], c["to"]) for c in upd["changes"]}
    assert changed["Vendor"] == ("Roadside Repair Co", "V")
    assert changed["Total"] == ("124.00", "10.00")
    assert diff["counts"]["changed"] == 1
    assert len(await db.list_work_orders(42)) == before  # no write


@pytest.mark.asyncio
async def test_project_resolves_via_passed_lookup_without_roster_sync(db):
    # No datatruck_trucks/trailers synced — a lookup passed by the engine
    # (built live from the rosters) resolves plate→unit + type.  Values are
    # lists, matching how the map round-trips through the JSON preview cache.
    lookup = {"abc123": ["T100", "truck"], "xyz789": ["TR200", "trailer"]}
    rows = [
        {"external_id": "6001", "vehicle_unit": "ABC123", "vendor_name": "V",
         "total_cost": 10.0, "line_items": []},
        {"external_id": "6002", "vehicle_unit": "XYZ789", "vendor_name": "V2",
         "total_cost": 20.0, "line_items": []},
    ]
    await db.project_external_work_orders(42, rows, vehicle_lookup=lookup)
    got = {r["external_id"]: r for r in await db.list_work_orders(42)}
    assert got["6001"]["vehicle_name"] == "T100"
    assert got["6001"]["vehicle_type"] == "truck"
    assert got["6002"]["vehicle_name"] == "TR200"
    assert got["6002"]["vehicle_type"] == "trailer"


# ── Company matching by MC / DOT ───────────────────────────────


@pytest.mark.asyncio
async def test_company_mc_dot_round_trip(db):
    aid = (await db.create_account("MC Co")).id  # companies FK → accounts
    c = await db.add_company(
        aid, code="CFT", display_name="Cargo Freight Trucking Inc",
        mc_number="100100", usdot_number="2002002",
    )
    assert c.mc_number == "100100"
    got = (await db.get_account_companies(aid, active_only=False))[0]
    assert got.mc_number == "100100"
    assert got.usdot_number == "2002002"
    # update one of them
    await db.update_company(c.id, account_id=aid, mc_number="9999999")
    again = await db.get_company_by_code(aid, "CFT")
    assert again.mc_number == "9999999"


@pytest.mark.asyncio
async def test_project_matches_company_by_mc_number(db):
    # The account owns the MC → a synced WO carrying that MC tags the company.
    aid = (await db.create_account("MC Co")).id
    await db.add_company(
        aid, code="CFT", display_name="Cargo Freight Trucking Inc",
        mc_number="100100", usdot_number="2002002",
    )
    rows = [{
        "external_id": "7001", "mc_number": "100100", "vehicle_unit": "204",
        "vendor_name": "V", "total_cost": 10.0, "line_items": [],
    }]
    await db.project_external_work_orders(aid, rows, source="datatruck")
    wo = (await db.list_work_orders(aid))[0]
    assert wo["company_code"] == "CFT"

    # The preview surfaces the resolved company NAME (not just the number).
    diff = await db.plan_external_work_orders(aid, rows)
    assert diff["update"][0]["company"] == "Cargo Freight Trucking Inc"


@pytest.mark.asyncio
async def test_project_unmatched_mc_leaves_company_blank(db):
    rows = [{
        "external_id": "7002", "mc_number": "0000000", "vehicle_unit": "X",
        "vendor_name": "V", "total_cost": 5.0, "line_items": [],
    }]
    await db.project_external_work_orders(42, rows, source="datatruck")
    wo = (await db.list_work_orders(42))[0]
    assert wo["company_code"] == ""   # no company owns that MC


@pytest.mark.asyncio
async def test_plan_detects_company_newly_matched(db):
    # A WO synced before any company owned its MC has a blank company.
    aid = (await db.create_account("Co")).id
    rows = [{
        "external_id": "8001", "mc_number": "555", "vehicle_unit": "T1",
        "vendor_name": "V", "total_cost": 10.0, "line_items": [],
    }]
    await db.project_external_work_orders(aid, rows, source="datatruck")
    assert (await db.list_work_orders(aid))[0]["company_code"] == ""

    # Operator adds the company that owns MC 555 → the preview must now
    # surface a Company change (so it isn't hidden as "no changes").
    await db.add_company(aid, code="ACME", display_name="Acme Trucking Inc",
                         mc_number="555")
    diff = await db.plan_external_work_orders(aid, rows)
    changed = {c["field"]: (c["from"], c["to"]) for c in diff["update"][0]["changes"]}
    assert changed["Company"] == ("", "Acme Trucking Inc")
    assert diff["counts"]["changed"] == 1


@pytest.mark.asyncio
async def test_project_stores_assigned_to(db):
    rows = [{
        "external_id": "9100", "assigned_to": "Pat Driver", "vehicle_unit": "T1",
        "vendor_name": "V", "total_cost": 5.0, "line_items": [],
    }]
    await db.project_external_work_orders(42, rows, source="datatruck")
    assert (await db.list_work_orders(42))[0]["assigned_to"] == "Pat Driver"


@pytest.mark.asyncio
async def test_reconcile_projects_orphaned_snapshot_rows(db):
    """Self-heal: a snapshot row that never reached the work_orders
    module (synced before the projection existed, or in a window later
    syncs moved past) gets projected from its stored payload.  Audit
    2026-07-23 found 114 such orphans in production."""
    from capabilities.integrations.datatruck.sync import (
        _norm_work_order, reconcile_work_orders_projection,
    )

    raw = {
        "id": 9901, "number": "WO-ORPHAN-1", "status": "completed",
        "vendor": "Orphan Repair Co", "trailer": "TL9",
        "scheduled_on_date": "2026-05-01T00:00:00Z",
        "total_price": 150.0, "balance": 0.0, "paid_amount": 150.0,
        "work_order_tasks": [
            {"custom_task": "Mudflap", "parts_price": 75.0,
             "parts_quantity": 2, "total_price": 150.0},
        ],
    }
    # Stage the snapshot WITHOUT projecting — the orphan state.
    n = await db.upsert_datatruck_work_orders(77, [_norm_work_order(raw)])
    assert n == 1
    assert await db.list_work_orders(77) == []

    healed = await reconcile_work_orders_projection(77, db)
    assert healed == 1
    rows = await db.list_work_orders(77)
    assert len(rows) == 1
    wo = rows[0]
    assert wo["external_id"] == "9901"
    assert wo["external_number"] == "WO-ORPHAN-1"
    assert wo["vendor_name"] == "Orphan Repair Co"
    assert wo["payment_status"] == "paid"      # balance-cleared seed
    parts = await db.list_work_order_parts(wo["id"])
    assert [p["part_name"] for p in parts] == ["Mudflap"]

    # Idempotent: nothing left to heal on the second pass.
    assert await reconcile_work_orders_projection(77, db) == 0


@pytest.mark.asyncio
async def test_payment_ratchets_forward_from_tms_balance(db):
    """Synced invoices are PAID in Datatruck (EFS/bank), so a cleared
    upstream balance marks the row paid on refresh — one-way only:
    a paid (or void) row is never demoted by a lagging feed.  Audit
    2026-07-23: 110 rows sat "Unpaid" forever under seed-once."""
    row = {**_WO_ROWS[0], "balance": 124.0}       # open balance → unpaid
    await db.project_external_work_orders(43, [row], source="datatruck")
    assert (await db.list_work_orders(43))[0]["payment_status"] == "unpaid"

    # Upstream balance clears → the refresh ratchets it to paid.
    await db.project_external_work_orders(
        43, [{**row, "balance": 0.0}], source="datatruck",
    )
    assert (await db.list_work_orders(43))[0]["payment_status"] == "paid"

    # One-way: re-syncing an open balance never demotes a paid row.
    await db.project_external_work_orders(43, [row], source="datatruck")
    assert (await db.list_work_orders(43))[0]["payment_status"] == "paid"


@pytest.mark.asyncio
async def test_datatruck_status_maps_to_fleetio_lifecycle(db):
    """The projection maps Datatruck's own status onto ours instead of
    flattening everything to one value (the pre-fix bug: all 583 synced
    WOs read 'submitted').  Unknown/blank → closed (shop invoices are
    completed work)."""
    rows = [
        {"external_id": "s1", "number": "WO-A", "vehicle_unit": "T1",
         "status": "completed", "total_cost": 10.0, "line_items": []},
        {"external_id": "s2", "number": "WO-B", "vehicle_unit": "T2",
         "status": "open", "total_cost": 10.0, "line_items": []},
        {"external_id": "s3", "number": "WO-C", "vehicle_unit": "T3",
         "status": "in progress", "total_cost": 10.0, "line_items": []},
        {"external_id": "s4", "number": "WO-D", "vehicle_unit": "T4",
         "total_cost": 10.0, "line_items": []},   # no status → closed
    ]
    await db.project_external_work_orders(60, rows, source="datatruck")
    by = {r["external_id"]: r["status"] for r in await db.list_work_orders(60)}
    assert by == {"s1": "completed", "s2": "open", "s3": "in_progress", "s4": "completed"}


@pytest.mark.asyncio
async def test_status_ratchets_to_completed_never_reopens(db):
    """A WO synced while upstream is 'in progress' auto-completes when
    the shop finishes it — one-way (never reopened by a lagging feed)."""
    row = {"external_id": "r1", "number": "WO-R", "vehicle_unit": "T1",
           "status": "in progress", "total_cost": 10.0, "line_items": []}
    await db.project_external_work_orders(61, [row], source="datatruck")
    assert (await db.list_work_orders(61))[0]["status"] == "in_progress"

    # Upstream finishes → ratchets to completed.
    await db.project_external_work_orders(
        61, [{**row, "status": "completed"}], source="datatruck")
    assert (await db.list_work_orders(61))[0]["status"] == "completed"

    # Never reopens: a lagging feed showing "open" again stays completed.
    await db.project_external_work_orders(
        61, [{**row, "status": "open"}], source="datatruck")
    assert (await db.list_work_orders(61))[0]["status"] == "completed"
