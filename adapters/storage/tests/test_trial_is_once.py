"""A trial is once per account, and "once" has to survive the ending.

The window closing clears ``trial_ends_at``, so after an expiry nothing
else on the row can tell a used trial from a never-used one — a second
one would look perfectly legitimate to any code that asked.  That is the
hole these close, and they close it at the STORE, because the two
callers today both create an account and a third one added tomorrow
would not think to ask.

The other disqualifier is paying.  An account that has been billed, or
that carries a provider subscription at all, must not be able to come
back round to a free fortnight.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_a_second_trial_is_refused_even_after_the_first_one_ended(pg_db):
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Twice Co", tier="free")

    assert await db.start_trial(acct.id, days=14), "the first one is theirs"
    sub = await db.get_subscription(acct.id)
    assert sub["status"] == "trialing" and sub["trial_started_at"]

    # the window closes the ordinary way
    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
    assert await db.expire_due_trials(before_iso=later)
    sub = await db.get_subscription(acct.id)
    assert sub["trial_ends_at"] is None, "the window is gone"
    assert sub["trial_started_at"], "the memory is not"

    assert await db.start_trial(acct.id, days=14) is None, "and there is no second one"
    assert (await db.get_subscription(acct.id))["status"] == "active"
    assert (await db.get_subscription(acct.id))["tier"] == "free"


@pytest.mark.asyncio
async def test_an_account_that_pays_is_never_put_back_on_trial(pg_db):
    """The operator's own case: someone comes back round to pay for Pro,
    and no route may hand them a free fortnight instead."""
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Paying Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", status="active",
                                 provider="stripe", provider_subscription_id="sub_live")

    assert await db.start_trial(acct.id, days=14) is None
    sub = await db.get_subscription(acct.id)
    assert sub["status"] == "active" and sub["tier"] == "pro", "untouched"

    v = await db.trial_eligibility(acct.id)
    assert v["eligible"] is False
    assert any("payment subscription" in r for r in v["reasons"])


@pytest.mark.asyncio
async def test_a_history_of_invoices_disqualifies_even_with_no_subscription(pg_db):
    """A cancelled customer keeps their invoices.  Those are the proof
    they were a customer, whatever the subscription row says now."""
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Lapsed Co", tier="free")
    await db.get_or_create_subscription(acct.id)
    await db.record_invoice(acct.id, "in_old_1", amount_due_cents=9900,
                            amount_paid_cents=9900, status="paid")

    v = await db.trial_eligibility(acct.id)
    assert v["eligible"] is False
    assert any("invoiced before" in r for r in v["reasons"])
    assert await db.start_trial(acct.id, days=14) is None


@pytest.mark.asyncio
async def test_a_trial_already_running_is_not_restarted(pg_db):
    """Otherwise the window slides forward on every call — a trial that
    never ends, from a button pressed twice."""
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Sliding Co", tier="free")
    first = await db.start_trial(acct.id, days=14)
    assert first
    assert await db.start_trial(acct.id, days=14) is None
    assert (await db.get_subscription(acct.id))["trial_ends_at"] == first, "the end did not move"


@pytest.mark.asyncio
async def test_trialing_without_our_stamp_is_still_a_running_trial(pg_db):
    """The state ``trial_started_at`` cannot speak for.

    Status is not only ours to set: the Stripe webhook writes whatever
    the provider reports, so a row can read ``trialing`` with no stamp of
    ours on it.  Treating that as "never had a trial" would start a
    second one on top of a live one, and this is the check that does not
    let it — the reason it is not redundant with the stamp above."""
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Provider Trial Co", tier="free")
    await db.get_or_create_subscription(acct.id)
    # exactly what a webhook reflecting a provider-side trial leaves
    await db.update_subscription(acct.id, tier="pro", status="trialing")
    sub = await db.get_subscription(acct.id)
    assert sub["status"] == "trialing" and not (sub["trial_started_at"] or "")

    v = await db.trial_eligibility(acct.id)
    assert v["eligible"] is False
    assert any("already running" in r for r in v["reasons"])
    assert await db.start_trial(acct.id, days=14) is None


@pytest.mark.asyncio
async def test_a_fresh_account_is_eligible_and_nothing_else_is_required(pg_db):
    """The signup path has to keep working — a refusal that caught every
    new account would be a trial nobody ever gets."""
    db = pg_db
    await db.upsert_plan("pro", label="Pro", included=["*"], trial_default=True,
                         price_monthly_cents=9900)
    acct = await db.create_account("Brand New Co", tier="free")
    v = await db.trial_eligibility(acct.id)
    assert v["eligible"] is True and v["reasons"] == []
    assert await db.start_trial(acct.id, days=14)
