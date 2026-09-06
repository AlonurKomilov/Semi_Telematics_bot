"""Scheduled Reports lives in its own home, and its URLs did not move.

The subscription API left the Reports hub router for
``capabilities/reporting/scheduled/`` — the Reports service's
sub-feature — so a permission on Reports has one place to cover.
Every client (dashboard, bot, mini app) still calls
``/user/scheduled-reports``.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from tests._repo import REPO


def test_the_routes_are_in_the_home_at_the_old_paths():
    from capabilities.reporting.scheduled import router as home
    routes = {(tuple(sorted(r.methods)), r.path) for r in home.user_router.routes}
    assert routes == {
        (("GET",), "/user/scheduled-reports"),
        (("PUT",), "/user/scheduled-reports"),
        (("DELETE",), "/user/scheduled-reports"),
    }


def test_the_hub_router_no_longer_carries_them():
    from capabilities.reporting import router as hub
    assert not hasattr(hub, "user_router")
    paths = {r.path for r in hub.router.routes}
    assert not any("scheduled" in p for p in paths)


def test_the_app_mounts_the_home():
    src = open(os.path.join(REPO, "interfaces/api/app.py"), encoding="utf-8").read()
    assert "scheduled_routes.user_router" in src
    assert "reports_routes.user_router" not in src
