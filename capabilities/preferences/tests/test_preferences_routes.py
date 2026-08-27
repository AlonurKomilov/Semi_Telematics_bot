"""Route parity for the preferences capability.

The four UI-preference endpoints were moved out of
``interfaces/api/routes/user.py`` into ``capabilities/preferences/router.py``.
The dashboard calls these paths by string and every row in
``user_preferences`` is addressed by them, so a path change is a silent
data-loss bug (saved tabs / column layouts stop resolving) rather than a
loud failure.  These tests pin the exact paths and methods.

They assert on the mounted route table only — no DB, no auth — so they
stay fast and can't be broken by unrelated fixture churn.
"""

from __future__ import annotations

from capabilities.preferences import router as preferences_router


EXPECTED = {
    ("/user/preferences/ui", "GET"),            # bulk read (prefix filter)
    ("/user/preferences/ui/{key}", "GET"),
    ("/user/preferences/ui/{key}", "PUT"),
    ("/user/preferences/ui/{key}", "DELETE"),
}


def _pairs(router) -> set[tuple[str, str]]:
    found = set()
    for route in router.routes:
        for method in getattr(route, "methods", set()) or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.add((route.path, method))
    return found


def test_preference_paths_are_byte_identical():
    """The capability exposes exactly the paths the dashboard calls."""
    assert _pairs(preferences_router.router) == EXPECTED


def test_preference_paths_left_the_user_router():
    """No duplicate definition stayed behind in routes/user.py — two
    handlers on one path would make which-one-wins depend on mount order."""
    from interfaces.api.routes import user as user_routes

    leftovers = {
        (path, method)
        for path, method in _pairs(user_routes.router)
        if path.startswith("/user/preferences/ui")
    }
    assert leftovers == set(), f"still in user.py: {leftovers}"


def test_typed_profile_preferences_endpoint_still_exists():
    """``PUT /user/preferences`` (language / timezone / DND) is a DIFFERENT,
    typed endpoint that backend logic reads — it must NOT have been swept
    into the opaque KV capability."""
    from interfaces.api.routes import user as user_routes

    assert ("/user/preferences", "PUT") in _pairs(user_routes.router)


def test_key_validation_rejects_unsafe_keys():
    from fastapi import HTTPException

    from capabilities.preferences.keys import validate_key, validate_prefix

    validate_key("table.maintenance-tasks.visibility")   # must not raise
    validate_prefix(None)
    validate_prefix("")
    validate_prefix("table.")

    for bad in ("", "a" * 201, "has space", "slash/es", "quote'd"):
        try:
            validate_key(bad)
        except HTTPException as exc:
            assert exc.status_code == 422
        else:  # pragma: no cover - guard
            raise AssertionError(f"accepted unsafe key: {bad!r}")
