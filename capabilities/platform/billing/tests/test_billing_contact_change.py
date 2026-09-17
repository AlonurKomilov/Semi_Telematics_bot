"""Moving the address every bill goes to.

It is the one field a customer writes on the Billing page and the one
where being wrong is expensive: invoices, receipts and payment-failure
notices all follow it.  It is also the first thing an account takeover
would change — redirect the paperwork and nobody notices the rest.

So these pin the two proofs and the ways each could be skipped: a code
the owner has to read out of their OWN inbox, a link the NEW address
has to open, and nothing moving until both are answered.  Plus the
drift that started it — a customer changing the address in Stripe's own
portal while our copy kept saying unknown@4truck.us.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage.billing_email_changes import (
    CODE_SENT, CONFIRM_SENT, mask_email,
)

OLD = "unknown@4truck.us"
NEW = "adam@premiertruckinggroup.com"


# ── what the page is allowed to say ────────────────────────────────

def test_the_page_says_where_the_code_went_without_saying_the_address():
    """The owner must recognise the inbox to go and read it; a session
    that should not be looking must not learn the address from us."""
    assert mask_email("adam@premiertruckinggroup.com") == "ad••@premiertruckinggroup.com"
    # never fewer than two bullets: one would announce that the local
    # part is exactly two characters long
    assert mask_email("jo@x.io") == "j••@x.io"
    assert mask_email("") == "" and mask_email("not-an-address") == ""
    # the domain is kept on purpose — it is what makes the hint usable
    assert mask_email("a@b.co").endswith("@b.co")


# ── the storage rules ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_code_is_not_stored_and_a_wrong_one_does_not_open_the_door(pg_db):
    db = pg_db
    acct = await db.create_account("Proof Co")
    code = await db.start_billing_email_change(
        acct.id, new_email=NEW, requested_by=7, requested_email="owner@co.com")
    assert len(code) == 6 and code.isdigit()
    row = await db._get_change_row(acct.id)
    assert code not in str(row), "the code itself is never written down"
    assert row["status"] == CODE_SENT and not row["confirm_token"]

    wrong = "0" * 6 if code != "000000" else "111111"
    assert await db.verify_billing_email_code(acct.id, wrong) is None
    assert (await db._get_change_row(acct.id))["status"] == CODE_SENT, \
        "a wrong code leaves the request alone so the owner can retype"
    token = await db.verify_billing_email_code(acct.id, code)
    assert token and len(token) > 20
    after = await db._get_change_row(acct.id)
    assert after["status"] == CONFIRM_SENT
    assert after["code_hash"] == "", "the code is spent, not reusable"


@pytest.mark.asyncio
async def test_the_link_works_once_and_only_for_its_own_account(pg_db):
    db = pg_db
    mine = await db.create_account("Mine Co")
    other = await db.create_account("Other Co")
    code = await db.start_billing_email_change(
        mine.id, new_email=NEW, requested_by=7, requested_email="owner@co.com")
    token = await db.verify_billing_email_code(mine.id, code)

    got = await db.confirm_billing_email_change(token)
    assert got == {"account_id": mine.id, "new_email": NEW, "requested_by": 7}
    assert await db.confirm_billing_email_change(token) is None, "one use"
    assert await db.pending_billing_email_change(mine.id) is None
    assert await db.pending_billing_email_change(other.id) is None
    # a token nobody minted is refused the same way a spent one is
    assert await db.confirm_billing_email_change("made-up") is None
    assert await db.confirm_billing_email_change("") is None


@pytest.mark.asyncio
async def test_asking_again_replaces_the_request_rather_than_stacking(pg_db):
    """Two live codes for one account means the older one is a second
    key nobody remembers issuing."""
    db = pg_db
    acct = await db.create_account("Twice Co")
    first = await db.start_billing_email_change(
        acct.id, new_email="a@x.com", requested_by=7, requested_email="owner@co.com")
    second = await db.start_billing_email_change(
        acct.id, new_email="b@x.com", requested_by=7, requested_email="owner@co.com")
    assert await db.verify_billing_email_code(acct.id, first) is None, "the old code is dead"
    assert await db.verify_billing_email_code(acct.id, second)
    assert (await db._get_change_row(acct.id))["new_email"] == "b@x.com"


@pytest.mark.asyncio
async def test_an_expired_code_and_an_expired_link_both_stop(pg_db):
    db = pg_db
    acct = await db.create_account("Expiry Co")
    code = await db.start_billing_email_change(
        acct.id, new_email=NEW, requested_by=7, requested_email="owner@co.com",
        ttl_minutes=-1)
    assert await db.verify_billing_email_code(acct.id, code) is None

    code = await db.start_billing_email_change(
        acct.id, new_email=NEW, requested_by=7, requested_email="owner@co.com")
    token = await db.verify_billing_email_code(acct.id, code, ttl_hours=-1)
    assert token
    assert await db.confirm_billing_email_change(token) is None, "the link went stale"


@pytest.mark.asyncio
async def test_the_code_belongs_to_the_person_who_asked(pg_db):
    """A second owner holding the same session cannot finish someone
    else's request — the row names who started it."""
    db = pg_db
    acct = await db.create_account("Whose Co")
    code = await db.start_billing_email_change(
        acct.id, new_email=NEW, requested_by=7, requested_email="owner@co.com")
    assert await db.verify_billing_email_code(acct.id, code, requested_by=8) is None
    assert await db.verify_billing_email_code(acct.id, code, requested_by=7)


@pytest.mark.asyncio
async def test_an_address_that_is_not_one_is_refused_before_a_send(pg_db):
    db = pg_db
    acct = await db.create_account("Shape Co")
    for bad in ("", "nope", "a@b", "a@.com", "two @spaces.com", "@x.com"):
        with pytest.raises(ValueError):
            await db.start_billing_email_change(
                acct.id, new_email=bad, requested_by=7, requested_email="owner@co.com")
    with pytest.raises(ValueError):
        await db.start_billing_email_change(
            acct.id, new_email=NEW, requested_by=7, requested_email="")
    assert await db.pending_billing_email_change(acct.id) is None


# ── the flow as a person walks it ──────────────────────────────────

@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    """An owner and an admin on one account, plus the mail we would send."""
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    db = pg_db
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    acct = await db.create_account("PREMIER TRUCKING GROUP INC", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, billing_email=OLD)
    owner = await db.create_user(960001, acct.id, role=Role.OWNER)
    admin = await db.create_user(960002, acct.id, role=Role.ADMIN)
    await db.set_user_email_password(owner.id, "owner@premier.com", "x")
    await db.set_user_email_password(admin.id, "admin@premier.com", "x")

    async def _token(user, role):
        # Setting an email bumps ``auth_version`` where the session
        # hardening has landed — a credential change kills live sessions
        # — so the token must claim the CURRENT one or every call 401s.
        # Asked of the database rather than hard-coded, because that
        # column is arriving on its own schedule and a fixed number
        # would be wrong on one side of it or the other.
        claims = {}
        if hasattr(db, "get_user_auth_state"):
            state = await db.get_user_auth_state(user.id, "")
            claims["auth_version"] = int((state or {}).get("auth_version") or 0)
        return create_jwt(user.telegram_id, acct.id, role, **claims)

    outbox: list[dict] = []
    import capabilities.platform.billing.contact_email as _ce
    monkeypatch.setattr(_ce, "send_change_code",
                        lambda **kw: (outbox.append({"kind": "code", **kw}), True)[1])
    monkeypatch.setattr(_ce, "send_confirm_link",
                        lambda **kw: (outbox.append({"kind": "link", **kw}), True)[1])

    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    async with AsyncClient(transport=ASGITransport(app=create_api()),
                           base_url="http://testserver") as client:
        yield {
            "client": client, "db": db, "acct": acct, "outbox": outbox,
            "owner_hdr": {"Authorization": f"Bearer {await _token(owner, 'owner')}"},
            "admin_hdr": {"Authorization": f"Bearer {await _token(admin, 'admin')}"},
        }


@pytest.mark.asyncio
async def test_the_page_learns_who_may_edit_from_the_rule_that_guards_the_door(api):
    """The Edit control is drawn from the summary's ``can_edit_contact``,
    which the server computes with the same predicate that refuses the
    change routes — so the button and the door can never disagree, and
    the page holds no role literal of its own."""
    c = api["client"]
    for hdr, expected in ((api["owner_hdr"], True), (api["admin_hdr"], False)):
        r = await c.get("/api/billing/summary", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json()["can_edit_contact"] is expected
    r = await c.post("/api/billing/email/change", headers=api["admin_hdr"], json={"email": NEW})
    assert r.status_code == 403, "the door says what the page said"


@pytest.mark.asyncio
async def test_the_owner_walks_it_and_the_address_moves_only_on_the_link(api):
    c, db, acct, outbox = api["client"], api["db"], api["acct"], api["outbox"]
    hdr = api["owner_hdr"]

    r = await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    assert r.status_code == 200, r.text
    pending = r.json()["pending"]
    assert pending["status"] == CODE_SENT and pending["new_email"] == NEW
    assert pending["code_sent_to"] == "ow•••@premier.com", "masked, and not the billing address"
    code_mail = outbox[-1]
    assert code_mail["kind"] == "code" and code_mail["to"] == "owner@premier.com", \
        "the code goes to the OWNER's inbox, never to the address being added"
    assert code_mail["new_email"] == NEW, "the mail names what is being changed"
    sub = await db.get_subscription(acct.id)
    assert sub["billing_email"] == OLD, "nothing has moved yet"

    r = await c.post("/api/billing/email/change/verify", headers=hdr,
                     json={"code": code_mail["code"]})
    assert r.status_code == 200, r.text
    assert r.json()["pending"]["status"] == CONFIRM_SENT
    link_mail = outbox[-1]
    assert link_mail["kind"] == "link" and link_mail["to"] == NEW
    assert link_mail["old_email"] == OLD, "the reader is told what it replaces"
    sub = await db.get_subscription(acct.id)
    assert sub["billing_email"] == OLD, "the owner's code alone does not move it"

    # the link — opened by whoever reads that inbox, with no session
    r = await c.get(f"/api/billing/email/confirm?token={link_mail['token']}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert NEW in r.text
    sub = await db.get_subscription(acct.id)
    assert sub["billing_email"] == NEW, "now it has moved"
    assert await db.pending_billing_email_change(acct.id) is None

    # and a second open of the same link says nothing about what it was
    again = await c.get(f"/api/billing/email/confirm?token={link_mail['token']}")
    assert again.status_code == 400 and NEW not in again.text
    rows = await db.list_platform_audit(event="billing_email_changed", limit=5)
    assert rows, "moving where a customer's money mail goes is written down"


@pytest.mark.asyncio
async def test_an_admin_can_read_the_bills_but_not_redirect_them(api):
    """Redirecting the paperwork is the first move of a takeover, so it
    is the owner's alone even though admins manage billing."""
    c, hdr = api["client"], api["admin_hdr"]
    assert (await c.get("/api/billing/email/change", headers=hdr)).status_code == 200
    r = await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    assert (await c.post("/api/billing/email/change/verify", headers=hdr,
                         json={"code": "123456"})).status_code == 403
    assert (await c.delete("/api/billing/email/change", headers=hdr)).status_code == 403


@pytest.mark.asyncio
async def test_a_wrong_code_says_one_thing_and_never_which_thing(api):
    c, hdr, outbox = api["client"], api["owner_hdr"], api["outbox"]
    await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    real = outbox[-1]["code"]
    wrong = "000000" if real != "000000" else "111111"
    r = await c.post("/api/billing/email/change/verify", headers=hdr, json={"code": wrong})
    assert r.status_code == 400
    assert r.json()["detail"] == "That code is wrong or has expired. Ask for a new one."
    # and the owner may still type the right one
    assert (await c.post("/api/billing/email/change/verify", headers=hdr,
                         json={"code": real})).status_code == 200


@pytest.mark.asyncio
async def test_the_change_can_be_called_off_and_its_link_dies_with_it(api):
    c, db, acct, hdr = api["client"], api["db"], api["acct"], api["owner_hdr"]
    await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    await c.post("/api/billing/email/change/verify", headers=hdr,
                 json={"code": api["outbox"][-1]["code"]})
    token = api["outbox"][-1]["token"]
    assert (await c.delete("/api/billing/email/change", headers=hdr)).status_code == 200
    assert (await c.get(f"/api/billing/email/confirm?token={token}")).status_code == 400
    assert (await db.get_subscription(acct.id))["billing_email"] == OLD
    assert (await c.delete("/api/billing/email/change", headers=hdr)).status_code == 404


@pytest.mark.asyncio
async def test_setting_it_to_what_it_already_is_is_refused_before_a_send(api):
    c, hdr, outbox = api["client"], api["owner_hdr"], api["outbox"]
    r = await c.post("/api/billing/email/change", headers=hdr, json={"email": OLD})
    assert r.status_code == 409 and "already the billing contact" in r.json()["detail"]
    assert outbox == [], "no mail for a change that is not one"


@pytest.mark.asyncio
async def test_the_confirm_link_needs_no_session_and_a_bare_one_is_refused(api):
    c = api["client"]
    assert (await c.get("/api/billing/email/confirm")).status_code == 400
    assert (await c.get("/api/billing/email/confirm?token=")).status_code == 400
    assert (await c.get("/api/billing/email/confirm?token=nope")).status_code == 400


# ── the drift that started all this ────────────────────────────────

def _fake_stripe(event):
    class _S:
        class Webhook:
            @staticmethod
            def construct_event(p, s, k):
                return event

        class error:  # noqa: N801
            class SignatureVerificationError(Exception):
                ...
    return _S


@pytest.mark.asyncio
async def test_an_address_changed_in_stripes_own_portal_comes_back_to_us(pg_db, monkeypatch):
    """"Manage payment" sends the customer to Stripe's portal, and the
    billing email is what they change there.  Without this event our row
    drifts: PREMIER read unknown@4truck.us for months while Stripe held
    the right address, so every receipt we sent went nowhere."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    acct = await db.create_account("Portal Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, provider_customer_id="cus_portal",
                                 billing_email=OLD)

    # the Customer IS the object here — there is no `customer` field to
    # follow, which is why the resolver has to read the id
    event = {"id": "evt_cust_1", "type": "customer.updated",
             "data": {"object": {"id": "cus_portal", "email": NEW}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe",
                        lambda: _fake_stripe(event))
    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out["handled"] is True, "the event must MATCH an account, not fall through"
    assert (await db.get_subscription(acct.id))["billing_email"] == NEW

    # an update that changes something else leaves the address alone
    other = {"id": "evt_cust_2", "type": "customer.updated",
             "data": {"object": {"id": "cus_portal", "email": NEW, "name": "Renamed"}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe",
                        lambda: _fake_stripe(other))
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert (await db.get_subscription(acct.id))["billing_email"] == NEW

    # and a Customer we do not know is acknowledged, never guessed at
    unknown = {"id": "evt_cust_3", "type": "customer.updated",
               "data": {"object": {"id": "cus_stranger", "email": "x@y.com"}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe",
                        lambda: _fake_stripe(unknown))
    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out["handled"] is False
    assert (await db.get_subscription(acct.id))["billing_email"] == NEW


def test_the_wiring_card_now_expects_the_event_that_keeps_us_in_step():
    """A check that does not ask for it lets an operator finish the
    go-live with the drift still in place."""
    from capabilities.platform.billing.setup_check import REQUIRED_EVENTS
    assert "customer.updated" in REQUIRED_EVENTS


# ── when the two proofs would land in one inbox ────────────────────

@pytest_asyncio.fixture
async def own_address(pg_db, monkeypatch):
    """The owner moving the bills to the address they sign in with.

    The real case that exposed this: the owner's 4truck login and the
    accounting inbox were the same mailbox, so the confirmation link
    arrived beside the code and read as a pointless repeat.
    """
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    db = pg_db
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    acct = await db.create_account("Same Inbox Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, billing_email=OLD)
    owner = await db.create_user(970001, acct.id, role=Role.OWNER)
    await db.set_user_email_password(owner.id, NEW, "x")   # signs in AS the new address

    outbox: list[dict] = []
    import capabilities.platform.billing.contact_email as _ce
    monkeypatch.setattr(_ce, "send_change_code",
                        lambda **kw: (outbox.append({"kind": "code", **kw}), True)[1])
    monkeypatch.setattr(_ce, "send_confirm_link",
                        lambda **kw: (outbox.append({"kind": "link", **kw}), True)[1])
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    claims = {}
    if hasattr(db, "get_user_auth_state"):
        st = await db.get_user_auth_state(owner.id, "")
        claims["auth_version"] = int((st or {}).get("auth_version") or 0)
    async with AsyncClient(transport=ASGITransport(app=create_api()),
                           base_url="http://testserver") as client:
        yield {"client": client, "db": db, "acct": acct, "owner": owner, "outbox": outbox,
               "hdr": {"Authorization":
                       f"Bearer {create_jwt(owner.telegram_id, acct.id, 'owner', **claims)}"}}


@pytest.mark.asyncio
async def test_the_owners_own_verified_address_needs_no_second_proof(own_address):
    """Mailing a link to the inbox that just answered the code proves
    nothing — it only makes the first answer look like it did not count."""
    c, db, acct = own_address["client"], own_address["db"], own_address["acct"]
    hdr, outbox = own_address["hdr"], own_address["outbox"]
    # verified the way a real user is: a token minted and redeemed
    owner = own_address["owner"]
    token = await db.create_email_verification_token(owner.id, NEW)
    assert await db.consume_email_verification_token(token) == owner.id
    assert await db.is_email_verified(owner.id)

    r = await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    assert r.status_code == 200, r.text
    assert r.json()["pending"]["needs_link"] is False, "the page must not promise a link"

    r = await c.post("/api/billing/email/change/verify", headers=hdr,
                     json={"code": outbox[-1]["code"]})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True and r.json()["pending"] is None
    assert [m["kind"] for m in outbox] == ["code"], "no second email was sent"
    assert (await db.get_subscription(acct.id))["billing_email"] == NEW
    assert await db.pending_billing_email_change(acct.id) is None
    rows = await db.list_platform_audit(event="billing_email_changed", limit=5)
    assert rows and "own_verified_signin_address" in str(rows[0]["details"])


@pytest.mark.asyncio
async def test_a_different_address_still_has_to_prove_itself(own_address):
    """The shortcut is narrow on purpose: any OTHER address is a party
    we have never heard from, and it still has to answer."""
    c, db, acct = own_address["client"], own_address["db"], own_address["acct"]
    hdr, outbox = own_address["hdr"], own_address["outbox"]
    # verified, exactly like the shortcut case — so the ONLY thing left
    # to stop the shortcut is that this is somebody else's address
    owner = own_address["owner"]
    assert await db.consume_email_verification_token(
        await db.create_email_verification_token(owner.id, NEW)) == owner.id
    r = await c.post("/api/billing/email/change", headers=hdr,
                     json={"email": "someone.else@elsewhere.com"})
    assert r.json()["pending"]["needs_link"] is True
    r = await c.post("/api/billing/email/change/verify", headers=hdr,
                     json={"code": outbox[-1]["code"]})
    assert r.json()["applied"] is False
    assert [m["kind"] for m in outbox] == ["code", "link"]
    assert outbox[-1]["to"] == "someone.else@elsewhere.com"
    assert (await db.get_subscription(acct.id))["billing_email"] == OLD, "still unmoved"


@pytest.mark.asyncio
async def test_an_unverified_signin_address_still_has_to_prove_itself(own_address):
    """The shortcut leans on the sign-in address being PROVEN.  An
    account that never redeemed its verification link has proven
    nothing, so the address gets asked like any stranger's would be."""
    c, db, acct = own_address["client"], own_address["db"], own_address["acct"]
    hdr, outbox = own_address["hdr"], own_address["outbox"]
    assert not await db.is_email_verified(own_address["owner"].id)

    r = await c.post("/api/billing/email/change", headers=hdr, json={"email": NEW})
    assert r.json()["pending"]["needs_link"] is True, "an unproven address is not a shortcut"
    r = await c.post("/api/billing/email/change/verify", headers=hdr,
                     json={"code": outbox[-1]["code"]})
    assert r.json()["applied"] is False
    assert [m["kind"] for m in outbox] == ["code", "link"]
    assert (await db.get_subscription(acct.id))["billing_email"] == OLD, "still unmoved"
