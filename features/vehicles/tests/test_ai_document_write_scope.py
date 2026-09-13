"""Guard: the document write resolves the truck, and under the approver.

Two defects on one action.

THE QUESTION CAME AFTER THE APPROVAL. The propose step never resolved
the truck, so an unknown unit number — or one two companies share — was
discovered only inside the executor, after somebody had already clicked
Approve on a card naming a truck the system could not find. "Say which
company" belongs where the model can still ask it.

THE EXECUTOR IGNORED THE APPROVER'S ACCESS. It hand-rolled the twin
check — correctly, but as a third copy — and scanned the whole roster,
without re-applying the approving user's vehicle scope, unlike every
sibling executor. A proposal made under one caller could be approved by
another who cannot see that truck at all.
"""

import pytest

from features.vehicles.documents.ai_actions import (
    _execute_file_vehicle_document, file_vehicle_document_action,
)


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def list_vehicles(self, account_id, **kw):
        return list(self._rows)


_ARGS = {"vehicle_name": "103", "doc_type": "insurance",
         "source_files": ["ins.pdf"]}


@pytest.mark.asyncio
async def test_an_ambiguous_number_is_asked_about_before_approval():
    res = await file_vehicle_document_action(
        dict(_ARGS), None, account_id=1, db=_DB([OSY, G1]))
    assert res.get("error"), res
    assert "proposed" not in res, "a card must not be offered for an unknown truck"


@pytest.mark.asyncio
async def test_a_resolved_truck_is_proposed_with_its_company():
    res = await file_vehicle_document_action(
        {**_ARGS, "company": "OSY"}, None, account_id=1, db=_DB([OSY, G1]))
    assert res.get("proposed") is True, res
    payload = res["artifacts"][-1]["payload"]
    assert payload["registry_id"] == 42
    assert payload["company"] == "OSY"


@pytest.mark.asyncio
async def test_the_executor_refuses_a_truck_the_approver_cannot_see():
    payload = {**_ARGS, "company": "G1"}
    ctx = {"user_id": 7, "scoped_vehicle_nums": ["103"],
           "scoped_vehicle_ladder": {"identities": [[42, "sam_42", "103"]]}}
    res = await _execute_file_vehicle_document(
        payload, 1, ctx, _DB([OSY, G1]))

    assert res["created"] is False, res
    assert "vehicle access" in res["message"].lower()


@pytest.mark.asyncio
async def test_the_executor_files_on_the_approvers_own_truck():
    payload = {**_ARGS, "company": "OSY"}
    ctx = {"user_id": 7, "scoped_vehicle_nums": ["103"],
           "scoped_vehicle_ladder": {"identities": [[42, "sam_42", "103"]]}}
    res = await _execute_file_vehicle_document(
        payload, 1, ctx, _DB([OSY, G1]))

    assert res["created"] is True, res


@pytest.mark.asyncio
async def test_an_unrestricted_approver_is_unaffected():
    payload = {**_ARGS, "company": "G1"}
    res = await _execute_file_vehicle_document(payload, 1, {"user_id": 7}, _DB([OSY, G1]))
    assert res["created"] is True, res


@pytest.mark.asyncio
async def test_the_approved_truck_is_the_one_filed_on():
    """The whole point of carrying registry_id: if the number comes to
    name a different truck between proposal and approval, the action
    refuses rather than filing on the new one."""
    proposal = await file_vehicle_document_action(
        {**_ARGS, "company": "OSY"}, None, account_id=1, db=_DB([OSY, G1]))
    payload = proposal["artifacts"][-1]["payload"]

    # The registry changed: 103 now belongs to a different row.
    moved = _V(77, "sam_77", "103", "OSY")
    res = await _execute_file_vehicle_document(
        payload, 1, {"user_id": 7}, _DB([moved]))

    assert res["created"] is False, res
    assert "approved for any more" in res["message"]
