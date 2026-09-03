"""The reviewer-account script's two safety rules, pinned without a database.

A Chrome Web Store reviewer signs in to a REAL account.  The script
that makes their user must (1) never hand them a write flag, whatever
the account's permission matrix says, and (2) make a password the
API's own policy accepts, typed by hand.
"""
from __future__ import annotations

import importlib

script = importlib.import_module("scripts.review_user")


def test_wide_flags_catches_every_write_invite_camera_and_account_wide_flag():
    perms = {
        "can_view_location": True,         # the one thing the reviewer is for
        "can_view_vehicle_docs": True,     # accepted, knowingly
        "can_manage_loads": True,          # a write — must be named
        "can_invite": True,                # brings a second stranger in
        "can_view_cameras": True,          # driver-facing video
        "can_events_all": True,            # account-wide, not own-truck
        "can_manage_geofence": False,      # off flags are not named
    }
    assert script.wide_flags(perms) == [
        "can_events_all", "can_invite", "can_manage_loads", "can_view_cameras",
    ]


def test_the_seeded_driver_role_has_no_wide_flag():
    """The premise of choosing DRIVER: nothing to write with.  If a
    future seed adds a can_manage_* to driver, this is where it shows."""
    import dataclasses
    from adapters.storage import Role
    from capabilities.permissions.roles import ROLE_PERMISSIONS
    seed = dataclasses.asdict(ROLE_PERMISSIONS[Role.DRIVER])
    assert seed["can_view_location"] is True
    assert script.wide_flags(seed) == []


def test_fleet_is_named_as_exposure_not_hidden():
    """The owner chose fleet for the reviewer.  The script must not
    pretend that is narrow: the seed carries write flags, and the
    same function that refuses them for driver must NAME them here."""
    import dataclasses
    from adapters.storage import Role
    from capabilities.permissions.roles import ROLE_PERMISSIONS
    seed = dataclasses.asdict(ROLE_PERMISSIONS[Role.FLEET])
    assert seed["can_view_location"] is True
    wide = script.wide_flags(seed)
    assert wide, "fleet has write flags; if this is ever empty, re-check wide_flags"
    assert any(k.startswith("can_manage_") for k in wide)


def test_owner_and_admin_are_never_a_reviewer_role():
    assert "owner" not in script.ROLES_ALLOWED and "admin" not in script.ROLES_ALLOWED
    assert script.ROLE_DEFAULT in script.ROLES_ALLOWED


def test_the_password_meets_the_policy_and_is_typeable():
    for _ in range(50):
        pw = script.new_password()
        assert len(pw) == 24
        assert any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw)
        assert pw.isalnum(), "no symbols — it is typed by hand into a form"


def test_timestamps_match_what_the_adapters_write():
    """The columns are TEXT and sorted as text; a Postgres now()::text
    would sort as the oldest row of its day."""
    ts = script.now_iso()
    assert "T" in ts and ts.endswith("+00:00")
