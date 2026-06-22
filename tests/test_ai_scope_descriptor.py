"""`_scope_descriptor` turns the server-resolved Vehicle Access scope
(``scoped_vehicle_nums``) into the trust-badge payload sent on the chat `done`
event.  None = unrestricted; a list = restricted to N vehicles ([] = no access).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.ai.router import _scope_descriptor


def test_unrestricted_when_scope_is_none_or_absent():
    assert _scope_descriptor({"scoped_vehicle_nums": None}) == {"restricted": False}
    assert _scope_descriptor({}) == {"restricted": False}
    assert _scope_descriptor(None) == {"restricted": False}


def test_restricted_reports_vehicle_count():
    assert _scope_descriptor({"scoped_vehicle_nums": ["B-1", "B-2", "B-3"]}) == {
        "restricted": True, "vehicle_count": 3,
    }


def test_restricted_to_none_is_zero_not_unrestricted():
    # [] means "explicitly scoped to nothing" — still restricted, count 0.
    assert _scope_descriptor({"scoped_vehicle_nums": []}) == {
        "restricted": True, "vehicle_count": 0,
    }
