"""Guard: inspections answer about ONE truck, and say which.

Unit numbers are reused across companies. The tool asked the store for
every inspection whose vehicle_name matched, so a caller asking about
"103" was shown BOTH companies' pre-trips interleaved — the other
company's drivers by name, their defect counts, their free-text notes —
with no company marker anywhere in the payload. Even ignoring the
disclosure, the defect summary the model narrated was a merge of two
trucks, so a defect on somebody else's truck was reported as this one's.

The rows carry no provider vehicle id, so the identity ladder could only
have reached its NAME rung here — the comparison that merged them. The
split goes through the company wall the adapter already exposes, in the
SQL, so the page and total stay honest.
"""

import pytest

from features.inspections.ai_tool import get_recent_inspections


class _V:
    def __init__(self, vid, unit, company):
        self.id, self.unit_number, self.company_code = vid, unit, company
        self.telematics_ref, self.is_active = f"sam_{vid}", True


OSY = _V(42, "103", "OSY")
G1 = _V(99, "103", "G1")


class _DB:
    def __init__(self, registry, rows):
        self._registry, self._rows = registry, rows
        self.only_user_ids = "unset"

    async def list_vehicles(self, account_id, **kw):
        return list(self._registry)

    async def list_inspections_for_account(self, account_id, **kw):
        self.only_user_ids = kw.get("only_user_ids")
        rows = self._rows
        uids = kw.get("only_user_ids")
        if uids is not None:
            rows = [r for r in rows if r["user_id"] in uids]
        return {"items": list(rows), "total": len(rows)}


def _insp(iid, user_id, name="103"):
    return {"id": iid, "user_id": user_id, "vehicle_name": name,
            "status": "submitted", "review_status": "", "defects_count": 1,
            "inspected_at": "2026-09-01", "inspection_type": "pre_trip",
            "notes": "brake light"}


@pytest.fixture
def _companies(monkeypatch):
    """user 7 drives for OSY, user 8 for G1."""
    class _P:
        async def get_all_user_company_codes(self, account_id):
            return {7: ["OSY"], 8: ["G1"]}

    import infra.platform as ip
    monkeypatch.setattr(ip, "get_platform_db", lambda: _P())


@pytest.mark.asyncio
async def test_only_the_named_companys_inspections_come_back(_companies):
    db = _DB([OSY, G1], [_insp(1, 7), _insp(2, 8)])
    res = await get_recent_inspections(
        {"vehicle_name": "103", "company": "OSY"}, None, account_id=1, db=db)

    assert db.only_user_ids == [7], db.only_user_ids
    assert res["count"] == 1
    assert res["inspections"][0]["id"] == 1
    assert res["filters"]["company"] == "OSY"


@pytest.mark.asyncio
async def test_an_ambiguous_number_asks_which_company(_companies):
    db = _DB([OSY, G1], [_insp(1, 7), _insp(2, 8)])
    res = await get_recent_inspections(
        {"vehicle_name": "103"}, None, account_id=1, db=db)
    assert res.get("error"), res
    assert "count" not in res


@pytest.mark.asyncio
async def test_a_truck_outside_the_callers_access_is_refused(_companies):
    db = _DB([OSY, G1], [_insp(1, 7), _insp(2, 8)])
    res = await get_recent_inspections(
        {"vehicle_name": "103", "company": "G1",
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)
    assert res.get("error"), res
    assert "vehicle access" in res["error"].lower()


@pytest.mark.asyncio
async def test_an_unregistered_truck_still_answers(_companies):
    """Retired, unregistered or mistyped: the registry cannot say, so the
    wall cannot be built and the name query stands."""
    db = _DB([], [_insp(1, 7, name="888")])
    res = await get_recent_inspections(
        {"vehicle_name": "888"}, None, account_id=1, db=db)
    assert db.only_user_ids is None
    assert res["count"] == 1
