"""One URL shape for config: ``/<feature>/config``.

Config used to be wherever each feature happened to put it —
``/kpi/thresholds``, ``/applications/dqf-config``,
``/vehicles/source-precedence``, ``/admin/scorecard-rules``,
``/admin/forum-routing/settings``, ``/admin/settings``,
``POST /object-storage/backend``.  Seven features, seven spellings, and
no way to answer "where do I change this?" except by reading each router.

The convention is now: **a feature's config lives at
``/<feature>/config``**, and the permission is the config family's
(``can_manage_config_all`` for account scope, ``can_manage_config_role``
for role scope) — never the feature's own Manage or View action.

RENAMED, NOT MOVED.  Every old path stays registered as a DEPRECATED
ALIAS pointing at the same handler function.  The API and the dashboard
ship from one repo but not in one instant: during a deploy a browser is
still running the previous JS bundle, and a hard rename would 404 every
config read in that window.  This is the same recipe the wire-key renames
use — a deprecated same-object alias plus a test that the alias and the
primary really are the same object.
"""

from __future__ import annotations

import os
import sys
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test" * 8)


# The whole API mounts TWICE — ``/api/v1`` and ``/api`` (app.py:434), so
# every route below appears under both.  Stripped here rather than written
# into every row, so the table states the ROUTE and stays readable.
# Longest first: stripping "/api" from "/api/v1/kpi/config" would leave
# "/v1/kpi/config" and quietly fail to match.
MOUNT_PREFIXES = ("/api/v1", "/api")


def _routes():
    """Every mounted route, as (methods, path, endpoint, deprecated).

    ``path`` has the mount prefix removed, so it matches what the router
    module declares.
    """
    from interfaces.api.app import create_api
    app = create_api()
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        endpoint = getattr(r, "endpoint", None)
        if not methods or endpoint is None:
            continue
        path = r.path
        for pref in MOUNT_PREFIXES:
            if path.startswith(pref):
                path = path[len(pref):]
                break
        out.append((methods, path, endpoint, getattr(r, "deprecated", None)))
    return out


# (primary_path, primary_method, legacy_path, legacy_method) — the pairs
# this migration created.  Listed explicitly so removing an alias is a
# deliberate edit to this table rather than something that quietly stops
# being tested.
#
# The method is per-side because one rename changed it: switching the
# storage backend was ``POST /object-storage/backend`` — a verb that
# suited "perform a switch" — and is now ``PUT /object-storage/config``,
# because under the convention it is a config value being replaced.  The
# old POST stays mounted and still works.
ALIAS_PAIRS = [
    ("/kpi/config", "GET", "/kpi/thresholds", "GET"),
    ("/kpi/config", "PUT", "/kpi/thresholds", "PUT"),
    ("/applications/config", "GET", "/applications/dqf-config", "GET"),
    ("/applications/config", "PUT", "/applications/dqf-config", "PUT"),
    ("/applications/config/reveal", "POST",
     "/applications/dqf-config/reveal", "POST"),
    ("/applications/config/reexport", "POST",
     "/applications/dqf-config/reexport", "POST"),
    ("/vehicles/config", "GET", "/vehicles/source-precedence", "GET"),
    ("/vehicles/config", "PUT", "/vehicles/source-precedence", "PUT"),
    ("/object-storage/status", "GET", "/object-storage/config", "GET"),
    ("/object-storage/config", "PUT", "/object-storage/backend", "POST"),
    ("/settings/config", "GET", "/admin/settings", "GET"),
    ("/settings/config", "PUT", "/admin/settings", "PUT"),
    ("/alerts/config", "PUT", "/admin/forum-routing/settings", "PUT"),
    ("/coaching/config/rules", "GET", "/coaching/rules", "GET"),
    ("/coaching/config/rules", "POST", "/coaching/rules", "POST"),
    ("/driver-pay/config/rules", "GET", "/driver-pay/rules", "GET"),
    ("/driver-pay/config/rules", "POST", "/driver-pay/rules", "POST"),
    ("/scorecards/config/rules", "GET", "/admin/scorecard-rules", "GET"),
    ("/scorecards/config/pillar-caps", "GET",
     "/admin/scorecard-pillar-caps", "GET"),
]


class TestAliasesAreTheSameObject:
    def test_every_legacy_path_resolves_to_the_primary_handler(self):
        """An alias that drifted into a COPY is worse than no alias: the
        two paths would diverge silently, and the old one is the one
        still being called during a deploy."""
        routes = _routes()
        by_key = {}
        for methods, path, endpoint, _dep in routes:
            for m in methods:
                by_key.setdefault((path, m), endpoint)

        missing, mismatched = [], []
        for primary, p_method, legacy, l_method in ALIAS_PAIRS:
            p_ep = by_key.get((primary, p_method))
            l_ep = by_key.get((legacy, l_method))
            if p_ep is None:
                missing.append(f"{p_method} {primary} (primary) is not mounted")
                continue
            if l_ep is None:
                missing.append(f"{l_method} {legacy} (alias) is not mounted")
                continue
            if p_ep is not l_ep:
                mismatched.append(
                    f"{primary} ({p_method}) -> {p_ep.__name__} but "
                    f"{legacy} ({l_method}) -> {l_ep.__name__}"
                )
        assert not missing, "\n  ".join(missing)
        assert not mismatched, (
            "alias and primary are different functions — the alias has "
            "drifted into a copy:\n  " + "\n  ".join(mismatched)
        )

    def test_legacy_paths_are_marked_deprecated(self):
        """The alias exists to survive one deploy, not forever.  Marking
        it deprecated is what makes it findable when we remove it."""
        routes = _routes()
        undeclared = []
        legacy_paths = {(lg, lm) for _p, _pm, lg, lm in ALIAS_PAIRS}
        for methods, path, _endpoint, deprecated in routes:
            for m in methods:
                if (path, m) in legacy_paths and not deprecated:
                    undeclared.append(f"{m} {path}")
        assert not undeclared, (
            "legacy config aliases not marked deprecated=True: "
            + ", ".join(sorted(undeclared))
        )


class TestConventionHolds:
    def test_every_config_path_is_feature_slash_config(self):
        """``/config`` must be preceded by a feature segment and nothing
        else — no ``/admin/...-config``, no ``/config`` at the root."""
        offenders = []
        for methods, path, _ep, deprecated in _routes():
            if deprecated:
                continue                       # aliases are the old shape
            if "/config" not in path:
                continue
            parts = [p for p in path.split("/") if p]
            if "config" not in parts:
                continue                       # e.g. /dqf-config, an alias
            i = parts.index("config")
            if i != 1:
                offenders.append(f"{sorted(methods)[0]} {path}")
        assert not offenders, (
            "config paths that are not /<feature>/config:\n  "
            + "\n  ".join(sorted(offenders))
        )


class TestConfigLivesInConfigPy:
    """A feature's config endpoints live in ``features/<x>/config.py``.

    The convention only holds if it cannot drift.  Without this, the next
    developer adds a config endpoint to ``router.py``, every other test
    stays green, and the file layout quietly stops matching the URLs.
    """

    def test_every_config_endpoint_is_defined_in_a_config_module(self):
        import inspect
        offenders = []
        for methods, path, endpoint, deprecated in _routes():
            if deprecated:
                continue
            parts = [p for p in path.split("/") if p]
            if len(parts) < 2 or parts[1] != "config":
                continue
            if parts[0] == "auth":
                continue        # pre-dates this family; a public login read
            mod = inspect.getsourcefile(endpoint) or ""
            if os.path.basename(mod) != "config.py":
                offenders.append(
                    f"{sorted(methods)[0]} {path} -> {os.path.relpath(mod, _REPO)}"
                )
        assert not offenders, (
            "config endpoints defined outside a config.py:\n  "
            + "\n  ".join(sorted(offenders))
            + "\nA feature's config belongs in features/<x>/config.py beside "
              "its router — see capabilities/config/docs/ARCHITECTURE.md."
        )


class TestConfigIsNotShadowed:
    """``/<feature>/config`` must RESOLVE to the config handler.

    This is the failure route-parity cannot see.  Several feature routers
    carry a parametric route — ``/vehicles/{vehicle_name}``, and nine in
    applications — and FastAPI matches in registration order across the
    whole app.  Mount a feature router before its config router and
    ``GET /vehicles/config`` resolves to the vehicle-detail handler with
    ``vehicle_name="config"``.

    Both routes still exist, so route counts and parity diffs stay
    IDENTICAL while the endpoint is dead.  Only resolution shows it.
    """

    def test_config_paths_resolve_to_their_config_handler(self):
        from interfaces.api.app import create_api
        app = create_api()
        checks = [
            ("/api/v1/vehicles/config", "GET"),        # vs /{vehicle_name}
            ("/api/v1/applications/config", "GET"),    # vs nine parametric
            ("/api/v1/kpi/config", "GET"),
            ("/api/v1/settings/config", "GET"),
        ]
        wrong = []
        for path, method in checks:
            scope = {
                "type": "http", "method": method, "path": path,
                "headers": [], "query_string": b"",
            }
            hit = None
            for r in app.routes:
                match, _ = r.matches(scope)
                if str(match) == "Match.FULL":
                    hit = getattr(r, "endpoint", None)
                    break
            name = getattr(hit, "__name__", repr(hit))
            if name not in ("get_config", "put_config"):
                wrong.append(f"{method} {path} resolves to {name}")
        assert not wrong, (
            "a parametric route is shadowing a config endpoint:\n  "
            + "\n  ".join(wrong)
            + "\nMount each config router BEFORE its feature router in "
              "interfaces/api/app.py."
        )
