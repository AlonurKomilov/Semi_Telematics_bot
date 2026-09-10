"""A write from the browser panel is as accountable as one from the desk.

The owner's first requirement of Inventory is not the list — it is
that when something changes, the record says who changed it.  The panel
is a SECOND way in, and a second way in is exactly how a trail grows a
hole: give the extension its own storage call one day and the Audit Log
quietly stops seeing half the writes, with nothing turning red.

So this pins the whole chain, from the route the panel actually calls to
the row the Audit Log actually renders:

    /extension/inventory-*  →  features.inventory.service  →
    vehicle_inventory_events  →  activity_trail facade  →  Audit Log

It lives in the repo-root suite because it crosses four layers and no
single package owns it.
"""
from __future__ import annotations

import pytest

from adapters.storage.models import Role
from interfaces.api.auth import EXTENSION_AUDIENCE, EXTENSION_SCOPE, create_jwt


async def _panel_user(pg_db, monkeypatch, account_id: int, user_id: int):
    """The `user` dict a panel request arrives with — minted as an
    extension token and put through the real dependency, so the test
    cannot accidentally hand the routes a dashboard identity."""
    from interfaces.api import deps
    import infra.platform as _cp

    monkeypatch.setattr(_cp, "_db", pg_db)

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(deps, "_is_revoked_with_cache", _not_revoked)

    class _Req:
        url = type("U", (), {"path": "/api/extension/inventory-verify"})()
        headers: dict = {}
        client = type("C", (), {"host": "127.0.0.1"})()
        cookies: dict = {}
        state = type("S", (), {})()

    token = create_jwt(1, account_id, "owner", user_id=user_id,
                       is_primary_owner=True,
                       aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)
    return dict(await deps.get_current_user(
        _Req(), authorization=f"Bearer {token}", auth_token=None))


@pytest.mark.asyncio
async def test_every_panel_write_reaches_the_audit_log_with_its_author(
    pg_db, monkeypatch,
):
    from interfaces.api.routes import extension as ext
    from capabilities.activity_trail.facade import account_activity

    acct = (await pg_db.create_account("Panel Audit Co")).id
    await pg_db.add_company(account_id=acct, code="PTG",
                            samsara_api_key="samsara_api_test_key_123",
                            display_name="Premier")
    owner = await pg_db.create_user(telegram_id=515151, account_id=acct,
                                    role=Role.OWNER)
    vid = await pg_db.add_vehicle(acct, unit_number="103", company_code="PTG")
    user = await _panel_user(pg_db, monkeypatch, acct, owner.id)

    # 1 — add, from beside the truck.
    added = await ext.extension_add_item(
        ext._AddBody(vehicle_id=vid, category="Camera", label="Tablet",
                     identifier="33434245"),
        user=user,
    )
    item_id = added["item_id"]

    # 2 — flag it, and 3 — check it.
    await ext.extension_set_item_status(
        ext._StatusBody(item_id=item_id, status="damaged", note="cracked"),
        user=user,
    )
    await ext.extension_verify_item(ext._ItemRef(item_id=item_id), user=user)

    rows = await account_activity(
        pg_db, acct, viewer_can_see={"inventory_item": True},
        allowed_companies=["PTG"], entity_type="inventory_item",
    )
    actions = [r["action"] for r in rows]
    assert actions == ["verified", "status_change", "installed"]   # newest first

    # THE POINT: every one of them names the person who did it.
    assert {r["actor_user_id"] for r in rows} == {owner.id}
    assert all(r["entity_id"] == str(item_id) for r in rows)

    # …and a status change carries the VALUES, not just the fact.
    flagged = rows[1]
    assert flagged["changes"]["status"] == {"from": "installed", "to": "damaged"}
    assert flagged["note"] == "cracked"


@pytest.mark.asyncio
async def test_an_edit_records_what_the_words_used_to_be(pg_db, monkeypatch):
    """Editing is the write the panel currently refuses, sending the
    person to the dashboard instead — so before it can be offered here,
    the trail has to survive it.

    A rename is the quietest way to make a loss disappear: change the
    serial and the item that went missing was never that item.  A trail
    row saying only "edited" cannot answer the one question the feature
    exists to answer, so the event carries the old and the new value the
    way a status change does.
    """
    from interfaces.api.routes import extension as ext
    from capabilities.activity_trail.facade import account_activity

    acct = (await pg_db.create_account("Edit Trail Co")).id
    await pg_db.add_company(account_id=acct, code="PTG",
                            samsara_api_key="samsara_api_test_key_123",
                            display_name="Premier")
    owner = await pg_db.create_user(telegram_id=626262, account_id=acct,
                                    role=Role.OWNER)
    vid = await pg_db.add_vehicle(acct, unit_number="103", company_code="PTG")
    user = await _panel_user(pg_db, monkeypatch, acct, owner.id)

    added = await ext.extension_add_item(
        ext._AddBody(vehicle_id=vid, category="Camera", label="Tablet",
                     identifier="33434245"),
        user=user,
    )
    item_id = added["item_id"]

    # Through the PANEL's own route, not the storage call underneath it —
    # the route is what a person presses, and the wall it puts in front
    # of the write is part of what is being proved.
    await ext.extension_edit_item(
        ext._EditBody(item_id=item_id, label="Dashcam", identifier="99999999"),
        user=user,
    )

    rows = await account_activity(
        pg_db, acct, viewer_can_see={"inventory_item": True},
        allowed_companies=["PTG"], entity_type="inventory_item",
    )
    edited = rows[0]
    assert edited["action"] == "edited"
    assert edited["actor_user_id"] == owner.id
    assert edited["changes"]["label"] == {"from": "Tablet", "to": "Dashcam"}
    assert edited["changes"]["identifier"] == {"from": "33434245", "to": "99999999"}


@pytest.mark.asyncio
async def test_the_panel_cannot_retire_or_move_an_item(pg_db, monkeypatch):
    """The two verbs that END an item's story stay at a desk.

    Editing came to the panel because correcting what a record SAYS is
    what the person beside the truck can do best.  Retiring and
    transferring are the opposite: they are how a loss gets tidied away
    — "it is on truck 5 now", "it was retired" — and a key that lives in
    a browser must not be able to say either, however senior the person
    holding it.

    Two lists enforce it and this checks both: the SCOPE says what a
    token may do, EXTENSION_ROUTES says where it may knock.  A manage
    flag wide enough to edit therefore still cannot reach remove.
    """
    from interfaces.api.auth import EXTENSION_ROUTES
    from interfaces.api.routes import extension as ext

    assert "/extension/inventory-edit" in EXTENSION_ROUTES
    for shut in ("/extension/inventory-remove", "/extension/inventory-transfer"):
        assert shut not in EXTENSION_ROUTES

    # …and no route module offers them under any other name.
    paths = {r.path for r in ext.router.routes if hasattr(r, "path")}
    assert not {p for p in paths if "remove" in p or "transfer" in p}
