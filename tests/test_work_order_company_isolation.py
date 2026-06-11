"""Per-company isolation on work-order WRITE endpoints.

The read paths (list / get / attachment up+download) already route through
``_require_visible_work_order``, which drops work orders belonging to a
company the caller isn't a member of.  The write handlers used to fetch by
``account_id`` only, so a user restricted to company X but holding
``can_work_orders_all`` could update/delete/modify a company-Y work order by
hitting its (sequential, enumerable) id directly — a within-account,
between-company IDOR on writes.

These tests pin the fix: the write handlers now go through the same helper,
so a cross-company id resolves to 404 (existence not leaked) while a
same-company / unrestricted caller proceeds normally.  Cross-ACCOUNT
isolation was never in question (the ``account_id`` scope still applies).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import features.work_orders.router as wo_router
from features.work_orders.router import (
    PartCreate,
    WorkOrderUpdate,
    add_part,
    delete_work_order,
    update_work_order,
)


class _FakeTenantDB:
    """Minimal tenant-DB stub — only the methods the handlers reach."""

    def __init__(self, wo: dict):
        self._wo = wo
        self.mutated = False

    async def get_work_order(self, work_order_id, account_id=None):
        # The real query is account-scoped; the stub returns the row
        # regardless so the test exercises the per-COMPANY layer on top.
        return self._wo

    async def update_work_order(self, work_order_id, account_id=None, **kw):
        self.mutated = True
        return True

    async def delete_work_order(self, work_order_id, account_id=None):
        self.mutated = True
        return True

    async def add_work_order_part(self, work_order_id, **kw):
        self.mutated = True
        return 1

    async def list_work_order_attachments(self, work_order_id):
        return []

    async def add_audit_log(self, *a, **kw):
        return None


# Restricted, non-owner manager with the account-wide work-order grant.
_USER = {"role": "fleet", "account_id": 1, "sub": "1"}


@pytest.fixture
def patch_helper_deps(monkeypatch):
    """can_for_account → True (caller has can_work_orders_all); company
    membership is set per-test via the returned setter."""
    async def _can(*a, **kw):
        return True

    monkeypatch.setattr(wo_router, "can_for_account", _can)

    def _set_allowed_companies(codes):
        async def _codes(user):
            return codes
        monkeypatch.setattr(wo_router, "get_user_company_codes", _codes)

    return _set_allowed_companies


@pytest.mark.asyncio
class TestWriteHandlersEnforceCompanyScope:
    async def test_update_cross_company_is_404(self, patch_helper_deps):
        patch_helper_deps(["X"])  # caller may see company X only
        db = _FakeTenantDB({"id": 5, "company_code": "Y", "vehicle_name": "T1"})
        with pytest.raises(HTTPException) as exc:
            await update_work_order(
                5, WorkOrderUpdate(notes="edit"), user=_USER, tenant_db=db,
            )
        assert exc.value.status_code == 404
        assert db.mutated is False  # never reached the write

    async def test_delete_cross_company_is_404(self, patch_helper_deps):
        patch_helper_deps(["X"])
        db = _FakeTenantDB({"id": 5, "company_code": "Y", "vehicle_name": "T1"})
        with pytest.raises(HTTPException) as exc:
            await delete_work_order(5, user=_USER, tenant_db=db)
        assert exc.value.status_code == 404
        assert db.mutated is False

    async def test_add_part_cross_company_is_404(self, patch_helper_deps):
        patch_helper_deps(["X"])
        db = _FakeTenantDB({"id": 5, "company_code": "Y", "vehicle_name": "T1"})
        with pytest.raises(HTTPException) as exc:
            await add_part(
                5, PartCreate(part_name="filter"), user=_USER, tenant_db=db,
            )
        assert exc.value.status_code == 404
        assert db.mutated is False

    async def test_update_same_company_proceeds(self, patch_helper_deps):
        patch_helper_deps(["Y"])  # caller is a member of company Y
        db = _FakeTenantDB({"id": 5, "company_code": "Y", "vehicle_name": "T1"})
        result = await update_work_order(
            5, WorkOrderUpdate(notes="edit"), user=_USER, tenant_db=db,
        )
        assert result == {"ok": True}
        assert db.mutated is True

    async def test_update_unrestricted_user_proceeds(self, patch_helper_deps):
        patch_helper_deps([])  # empty = no company restriction (sees all)
        db = _FakeTenantDB({"id": 5, "company_code": "Y", "vehicle_name": "T1"})
        result = await update_work_order(
            5, WorkOrderUpdate(notes="edit"), user=_USER, tenant_db=db,
        )
        assert result == {"ok": True}
        assert db.mutated is True
