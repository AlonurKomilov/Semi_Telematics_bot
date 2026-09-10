"""The two fault-report handlers read Team Management's width.

``cmd_vehicle_report`` and ``cmd_critical`` asked only ``can_view_faults``:
a member narrowed to their own truck could type any unit number and take
its PDF, or pull the account's critical list.  Both now ask ``unit_width``
like every other bot surface; the narrowing itself is a pure helper."""

from __future__ import annotations

import os
import re

os.environ.setdefault("ENCRYPTION_KEY", "")

from interfaces.bot.vehicles import _narrow_rows, _row_truck
from tests._repo import REPO


def test_a_row_names_its_truck_in_any_of_the_three_shapes():
    assert _row_truck({"vehicle_name": " T-12 "}) == "t-12"
    assert _row_truck({"vehicle": {"name": "T-12"}}) == "t-12"
    assert _row_truck({"name": "T-12"}) == "t-12"
    assert _row_truck({}) == ""


def test_narrowing_is_exact_and_fails_closed():
    rows = [{"vehicle_name": "230"}, {"vehicle_name": "2303"}, {"vehicle": {"name": "230"}}]
    assert _narrow_rows(rows, "230") == [rows[0], rows[2]]     # never the substring match
    assert _narrow_rows(rows, " 230 ") == [rows[0], rows[2]]
    assert _narrow_rows(rows, None) == []                       # no truck → nothing
    assert _narrow_rows(rows, "") == []


def _handler_source(name: str) -> str:
    src = open(os.path.join(REPO, "interfaces/bot/vehicles.py"), encoding="utf-8").read()
    m = re.search(rf"async def {name}\(.*?(?=\n@_require_registered|\Z)", src, re.S)
    assert m, name
    return m.group(0)


def test_both_handlers_ask_the_width():
    for name in ("cmd_vehicle_report", "cmd_critical"):
        body = _handler_source(name)
        assert '_unit_width(user.account_id, user.role, user, "vehicles")' in body, name
    assert "vehicle_name = user.truck_num" in _handler_source("cmd_vehicle_report")
    assert "_narrow_rows(critical, user.truck_num)" in _handler_source("cmd_critical")
