"""The company wall binds every verb on the vehicle registry.

Team Management restricts a user to a set of companies, and that
restriction is an ACCESS boundary rather than a viewing one — its own
endpoint calls it "company access". Thirteen read routes in
features/vehicles/router.py honoured it. The six registry-ADMIN routes
never had, so a user restricted to company A could rename, archive,
restore, or read the VIN and plate of company B's trucks: writes wider
than reads, on data the same user cannot open anywhere else in the
product. Multi-company accounts here are often separate legal entities
sharing one login.

These pin the contract itself rather than any one endpoint, because the
drift happened precisely where nothing was written down.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
from fastapi import HTTPException

from interfaces.api.deps import filter_by_company_map, validate_company_access


class TestTheWallItself:

    def test_an_unrestricted_user_passes_everything(self):
        """Empty allow-list means no restriction — owners, and anyone
        Team Management never narrowed."""
        validate_company_access([], "ANYTHING")           # no raise
        rows = [{"registry_id": 1}, {"registry_id": 2}]
        assert filter_by_company_map(rows, [], {1: "G1"}, key="registry_id") == rows

    def test_a_restricted_user_is_refused_a_foreign_company(self):
        with pytest.raises(HTTPException) as e:
            validate_company_access(["G1"], "OSY")
        assert e.value.status_code == 403

    def test_a_restricted_user_passes_their_own_company(self):
        validate_company_access(["G1"], "G1")             # no raise

    def test_rows_of_a_known_foreign_company_are_dropped(self):
        rows = [{"registry_id": 1}, {"registry_id": 2}]
        kept = filter_by_company_map(
            rows, ["G1"], {1: "G1", 2: "OSY"}, key="registry_id")
        assert kept == [{"registry_id": 1}]

    def test_an_unresolved_row_is_kept_not_hidden(self):
        """The helper's documented fail-open. A device nobody has placed
        yet is exactly what the identity card exists to surface, so an
        unplaced row must not vanish behind a wall it cannot be judged
        against."""
        rows = [{"registry_id": None}, {"registry_id": 99}]
        assert filter_by_company_map(
            rows, ["G1"], {1: "G1"}, key="registry_id") == rows

    def test_a_cold_map_hides_nothing(self):
        rows = [{"registry_id": 1}]
        assert filter_by_company_map(rows, ["G1"], {}, key="registry_id") == rows


class TestNullCompanyIsUnscoped:
    """A vehicle with no company_code belongs to no company, so it is
    manageable by anyone holding the permission — the same answer
    inventory/router.py:_resolve_vehicle gives. On the live account 87 of
    188 active vehicles are in this state, so treating null as "denied"
    would have hidden nearly half the fleet from every restricted user.
    """

    def test_a_null_company_row_survives_the_filter(self):
        rows = [{"registry_id": 7}]
        # 7 is absent from the map, which is how _registry_company_map
        # represents "no company" — omitted rather than mapped to ''.
        assert filter_by_company_map(
            rows, ["G1"], {1: "G1"}, key="registry_id") == rows

    def test_creating_without_a_company_is_allowed_when_unrestricted(self):
        validate_company_access([], None)                 # no raise
        validate_company_access([], "")                   # no raise


class TestFailureModes:
    """404 for an id the caller may not see; 403 only where they named
    the company themselves. Mixing them is what leaks: a 403 on a foreign
    id confirms the id exists, which is the disclosure the wall exists to
    prevent."""

    def test_supplied_company_refuses_with_403_not_404(self):
        """POST /vehicles/ takes company_code in the body, so there is no
        resource whose existence a 404 would be hiding."""
        with pytest.raises(HTTPException) as e:
            validate_company_access(["G1"], "OSY")
        assert e.value.status_code == 403

    def test_the_id_routes_use_404(self):
        """Pinned as documentation: _wall_registry_vehicle raises 404,
        matching inventory's _resolve_vehicle, and this test fails if
        someone 'improves' it to 403."""
        import ast
        import inspect
        from features.vehicles import router as vr
        # Read the RAISES, not the prose — the docstring says "404 rather
        # than 403", so a substring check trips on its own explanation.
        tree = ast.parse(inspect.getsource(vr._wall_registry_vehicle).lstrip())
        codes = [
            node.exc.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and getattr(node.exc.func, "id", "") == "HTTPException"
            and node.exc.args and isinstance(node.exc.args[0], ast.Constant)
        ]
        assert codes and set(codes) == {404}, codes
