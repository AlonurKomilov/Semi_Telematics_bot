"""Guard: the documents report is scoped by identity and asks for a real type.

Two defects.

THE STRONGEST RUNG WAS UNREACHABLE. The scope row carried the registry
id under `id`, and the ladder reads it under `registry_id` — so the only
rung that can split two companies' trucks sharing a unit number never
fired, and the filter fell back to the name. `id` has to stay what it
is, because vehicle_documents.vehicle_id is the REGISTRY id despite
reading like a provider one, so `registry_id` is added beside it.

A NEAR MISS REPORTED THE WHOLE FLEET NON-COMPLIANT. missing_type was
free text compared RAW against the stored column, which holds twelve
fixed keys. "annual inspection" or "cab card" matched nothing, so the
`have` set was empty and EVERY active truck came back as missing that
document — a fleet-wide compliance failure that did not exist. The enum
is derived from the stored vocabulary, spaces and hyphens are
normalised, and anything else is an error rather than a false alarm.
"""

import pytest

from features.vehicles.documents.ai_tool import get_vehicle_documents_status


class _V:
    def __init__(self, vid, unit, company):
        self.id, self.unit_number, self.company_code = vid, unit, company
        self.telematics_ref, self.is_active = f"sam_{vid}", True


OSY = _V(42, "103", "OSY")
G1 = _V(99, "103", "G1")


class _DB:
    def __init__(self, vehicles, docs):
        self._vehicles, self._docs = vehicles, docs

    async def list_vehicles(self, account_id, **kw):
        return list(self._vehicles)

    async def list_account_vehicle_documents(self, account_id):
        return list(self._docs)


def _doc(vehicle_id, doc_type="insurance"):
    return {"vehicle_id": vehicle_id, "doc_type": doc_type,
            "expires_at": "2030-01-01", "vehicle_name": "103"}


@pytest.mark.asyncio
async def test_the_registry_id_splits_the_twins():
    """Pinned to OSY's 103; G1's must not be counted or reported."""
    db = _DB([OSY, G1], [])
    res = await get_vehicle_documents_status(
        {"missing_type": "insurance",
         "_scope_vehicles": ["103"],
         "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=db)

    assert res["total_vehicles"] == 1, res
    assert res["missing_count"] == 1


@pytest.mark.asyncio
async def test_a_truck_with_the_document_is_not_reported_missing():
    db = _DB([OSY], [_doc(42, "insurance")])
    res = await get_vehicle_documents_status(
        {"missing_type": "insurance"}, None, account_id=1, db=db)
    assert res["missing_count"] == 0, res


@pytest.mark.asyncio
async def test_a_near_miss_is_refused_not_answered_as_a_fleet_failure():
    db = _DB([OSY, G1], [_doc(42, "annual_inspection"), _doc(99, "annual_inspection")])
    res = await get_vehicle_documents_status(
        {"missing_type": "DOT inspection"}, None, account_id=1, db=db)

    assert res.get("error"), res
    assert "missing_type must be one of" in res["error"]
    assert "missing_count" not in res


@pytest.mark.asyncio
async def test_a_spaced_spelling_is_normalised_rather_than_refused():
    db = _DB([OSY], [_doc(42, "cab_card")])
    res = await get_vehicle_documents_status(
        {"missing_type": "cab card"}, None, account_id=1, db=db)
    assert res.get("missing_count") == 0, res
