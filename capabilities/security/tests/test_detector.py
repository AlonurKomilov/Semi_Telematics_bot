"""Each rule fires on the shape it is named for, and on nothing else.

The rules were written against a real attack (2026-09-08) and their
thresholds sit just under what that run actually did, so the run trips
every one and an ordinary customer trips none. These tests hold both
halves: a burst fires, a normal week does not.

The acceptance test that matters most cannot live here — it is the
detector pointed at production history, where it surfaced 33/33 of the
probe accounts and a second run nobody had noticed. What lives here is
the guarantee that each rule keeps meaning what its name says.
"""

from __future__ import annotations

import pytest

from capabilities.security import detector as D


# ── the vocabulary ────────────────────────────────────────────────

@pytest.mark.parametrize("email,expected", [
    ("a@guerrillamailblock.com", True),
    ("pentest_a@wearehackerone.com", True),
    ("masstest@example.com", True),
    ("x@yopmail.com", True),
    ("someone@thing.test", True),
    ("owner@premiertruckinggroup.com", False),
    ("adam@gmail.com", False),
    ("", False),
    (None, False),
    ("no-at-sign", False),
])
def test_suspect_domains(email, expected):
    assert D._suspect_domain(email) is expected


@pytest.mark.parametrize("ua", [
    "curl/8.19.0", "python-requests/2.32.5", "Go-http-client/1.1",
    "okhttp/4.9", "PostmanRuntime/7.36", "axios/1.6",
])
def test_non_browser_user_agents_are_recognised(ua):
    assert D.NON_BROWSER_UA.search(ua)


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
])
def test_real_browsers_are_not_flagged(ua):
    assert not D.NON_BROWSER_UA.search(ua)


@pytest.mark.parametrize("payload", [
    "Unknown company: ' OR '1'='1",
    "Unknown company: '; SELECT pg_sleep(5)--",
    "Unknown company: ' UNION SELECT NULL--",
    "days=365 vehicle==cmd|'/C calc",
    "GET /../../../../etc/passwd",
    "<script>alert(1)</script>",
])
def test_injection_markers(payload):
    assert D.INJECTION_MARKERS.search(payload)


@pytest.mark.parametrize("ordinary", [
    "Could not read the vehicle registry just now",
    "'MultiCompanyClient' object has no attribute 'get_fault_codes'",
    "Unknown company: TESTCO-A",
])
def test_ordinary_errors_are_not_injection(ordinary):
    assert not D.INJECTION_MARKERS.search(ordinary)


# ── rules against real Postgres ───────────────────────────────────

async def _signup(db, name, ip, email):
    """One self-serve signup, as /auth/register-account leaves it.

    ``is_primary_owner`` is set directly: storage has no keyword for it
    (a migration backfills role='owner' → the flag), and the rule joins
    on it because the account's CREATOR is what a throwaway domain
    implicates — an invited dispatcher's address says nothing about how
    the account was opened.
    """
    from adapters.storage import Role
    acct = await db.create_account(name)
    user = await db.create_user_with_email(
        email=email, password_hash="x", account_id=acct.id,
        role=Role.OWNER, display_name="o",
    )
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (user.id,))
    await db._db.commit()
    await db.add_platform_audit(
        "account_created", account_id=acct.id, actor="self-serve",
        details=f"name={name!r} owner={email} ip={ip}")
    return acct


@pytest.mark.asyncio
async def test_signup_burst_fires_above_the_threshold_and_names_every_account(seeded_db):
    db = seeded_db["db"]
    made = [await _signup(db, f"Burst{i}", "203.0.113.7", f"b{i}@example.com")
            for i in range(D.T_SIGNUPS_PER_IP + 2)]
    sigs = await D.rule_signup_burst(db._db, hours=24)
    assert {s.account_id for s in sigs} >= {a.id for a in made}
    assert all(s.ip == "203.0.113.7" and s.severity == "high" for s in sigs)


@pytest.mark.asyncio
async def test_signup_burst_stays_quiet_at_the_threshold(seeded_db):
    db = seeded_db["db"]
    for i in range(D.T_SIGNUPS_PER_IP):
        await _signup(db, f"Calm{i}", "203.0.113.8", f"c{i}@realcompany.com")
    sigs = await D.rule_signup_burst(db._db, hours=24)
    assert not [s for s in sigs if s.ip == "203.0.113.8"]


@pytest.mark.asyncio
async def test_disposable_email_flags_the_owner_domain(seeded_db):
    db = seeded_db["db"]
    bad = await _signup(db, "Throwaway Co", "203.0.113.9", "zz@guerrillamailblock.com")
    good = await _signup(db, "Real Co", "203.0.113.9", "ops@realcompany.com")
    hits = {s.account_id for s in await D.rule_disposable_email(db._db, hours=24)}
    assert bad.id in hits and good.id not in hits


@pytest.mark.asyncio
async def test_lockout_storm_fires_per_ip(seeded_db):
    db = seeded_db["db"]
    for _ in range(D.T_LOCKOUTS_PER_IP + 1):
        await db.record_login_attempt(
            user_id=None, email="v@x.com", success=False,
            failure_reason="account_locked", ip_address="203.0.113.10",
            user_agent="curl/8.19.0")
    sigs = await D.rule_lockout_storm(db._db, hours=24)
    hit = next(s for s in sigs if s.ip == "203.0.113.10")
    assert hit.count > D.T_LOCKOUTS_PER_IP and hit.severity == "high"


@pytest.mark.asyncio
async def test_non_browser_auth_flags_the_tool_not_the_browser(seeded_db):
    db = seeded_db["db"]
    await db.record_login_attempt(user_id=None, email="a@x.com", success=True,
                                  ip_address="203.0.113.11", user_agent="curl/8.19.0")
    await db.record_login_attempt(user_id=None, email="b@x.com", success=True,
                                  ip_address="203.0.113.12",
                                  user_agent="Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36")
    ips = {s.ip for s in await D.rule_non_browser_auth(db._db, hours=24)}
    assert "203.0.113.11" in ips and "203.0.113.12" not in ips


@pytest.mark.asyncio
async def test_reset_flood_fires_per_user(seeded_db):
    db, owner = seeded_db["db"], seeded_db["owner"]
    for _ in range(D.T_RESETS_PER_USER + 1):
        await db.create_password_reset_token(owner.id)
    sigs = await D.rule_reset_flood(db._db, hours=24)
    assert any(s.account_id == owner.account_id and s.count > D.T_RESETS_PER_USER
               for s in sigs)


@pytest.mark.asyncio
async def test_ledger_rules_are_silent_before_the_ledger_existed(seeded_db):
    """Honest emptiness: these read security_requests, which only fills
    from the day the recorder shipped — they enrich forward, not back."""
    db = seeded_db["db"]
    assert await D.rule_operator_probing(db._db, hours=24) == []
    assert await D.rule_privilege_sweep(db._db, hours=24) == []


@pytest.mark.asyncio
async def test_operator_probing_fires_once_the_ledger_has_rows(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    for _ in range(D.T_SYSTEM_403_PER_SRC + 1):
        await db.record_security_request(
            method="GET", path="/api/system/accounts", status=403,
            account_id=acct.id, ip="203.0.113.13")
    sigs = await D.rule_operator_probing(db._db, hours=24)
    assert any(s.account_id == acct.id and s.severity == "high" for s in sigs)


# ── aggregation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_candidates_rank_by_weight_and_carry_the_current_kind(seeded_db):
    db = seeded_db["db"]
    loud = await _signup(db, "Loud Co", "203.0.113.20", "loud@guerrillamailblock.com")
    for i in range(D.T_SIGNUPS_PER_IP + 1):
        await _signup(db, f"Filler{i}", "203.0.113.20", f"f{i}@realcompany.com")
    await db.update_account(loud.id, kind="monitored")

    cands = await D.find_candidates(db, hours=24)
    by_id = {c["account_id"]: c for c in cands if c["account_id"]}
    assert loud.id in by_id
    top = by_id[loud.id]
    assert {"signup_burst", "disposable_email"} <= set(top["rules"])
    assert top["kind"] == "monitored", "an already-watched account is annotated, not hidden"
    assert top["name"] == "Loud Co"
    weights = [c["weight"] for c in cands]
    assert weights == sorted(weights, reverse=True), "ranked by weight"


@pytest.mark.asyncio
async def test_a_broken_rule_does_not_blind_the_rest(seeded_db, monkeypatch, caplog):
    """The first run of this module returned zero candidates because every
    rule was raising and the swallow made it look like a clean result."""
    db = seeded_db["db"]
    await _signup(db, "Still Seen", "203.0.113.21", "s@guerrillamailblock.com")

    async def boom(_db, _hours):
        raise RuntimeError("column gone")

    monkeypatch.setattr(D, "ALL_RULES", (boom, D.rule_disposable_email), raising=True)
    with caplog.at_level("WARNING"):
        cands = await D.find_candidates(db, hours=24)
    assert any(c["name"] == "Still Seen" for c in cands)
    assert any("boom" in r.getMessage() for r in caplog.records), (
        "the failure must be logged, not silent")


@pytest.mark.asyncio
async def test_every_rule_failing_is_logged_as_an_error(seeded_db, monkeypatch, caplog):
    db = seeded_db["db"]

    async def boom(_db, _hours):
        raise RuntimeError("handle mistyped")

    monkeypatch.setattr(D, "ALL_RULES", (boom,), raising=True)
    with caplog.at_level("ERROR"):
        assert await D.find_candidates(db, hours=24) == []
    assert any("EVERY rule failed" in r.getMessage() for r in caplog.records), (
        "an empty result from a broken detector must not read as 'clean'")


# ── noise discipline: what opens a candidate, and what merely sharpens one ──

@pytest.mark.asyncio
async def test_ip_rotation_fires_on_one_email_from_many_ips(seeded_db):
    """The 2026-09-10 pattern: fifty IPs for one address in fourteen
    seconds, every one inside a private range — a spoofed XFF rotated to
    defeat the per-IP limit."""
    db = seeded_db["db"]
    for i in range(D.T_IPS_PER_EMAIL + 2):
        await db.record_login_attempt(
            user_id=None, email="victim@targetcompany.com", success=False,
            failure_reason="no_such_email", ip_address=f"10.20.0.{i}",
            user_agent="python-requests/2.32.5")
    sigs = await D.rule_ip_rotation(db._db, hours=24)
    hit = next(s for s in sigs if s.subject == "victim@targetcompany.com")
    assert hit.severity == "high" and hit.count > D.T_IPS_PER_EMAIL


@pytest.mark.asyncio
async def test_a_person_logging_in_from_a_few_places_is_not_flagged(seeded_db):
    db = seeded_db["db"]
    for ip in ("87.192.238.227", "70.61.187.233", "2603:6013:7d00::1"):
        await db.record_login_attempt(
            user_id=None, email="adam@premiertruckinggroup.com", success=True,
            ip_address=ip, user_agent="Mozilla/5.0 (Windows NT 10.0)")
    sigs = await D.rule_ip_rotation(db._db, hours=24)
    assert not [s for s in sigs if s.subject == "adam@premiertruckinggroup.com"]


@pytest.mark.asyncio
async def test_a_lone_curl_login_opens_no_candidate(seeded_db):
    """`non_browser_auth` is true of our own smoke tests, and on
    2026-09-10 it produced seventy-two near-identical rows because each
    spoofed IP became its own subject.  Low signals enrich, never open."""
    db = seeded_db["db"]
    await db.record_login_attempt(
        user_id=None, email="tool@ours.com", success=True,
        ip_address="203.0.113.30", user_agent="curl/8.19.0")
    cands = await D.find_candidates(db, hours=24)
    assert not [c for c in cands if c["ip"] == "203.0.113.30"]


@pytest.mark.asyncio
async def test_a_low_signal_attaches_to_a_candidate_another_rule_opened(seeded_db):
    db = seeded_db["db"]
    for i in range(D.T_LOCKOUTS_PER_IP + 1):
        await db.record_login_attempt(
            user_id=None, email="v@x.com", success=False,
            failure_reason="account_locked", ip_address="203.0.113.31",
            user_agent="curl/8.19.0")
    cands = {c["ip"]: c for c in await D.find_candidates(db, hours=24) if c["ip"]}
    hit = cands["203.0.113.31"]
    assert "lockout_storm" in hit["rules"], "the med/high rule opened it"
    assert "non_browser_auth" in hit["rules"], "the low rule sharpened it"


@pytest.mark.asyncio
async def test_an_account_less_hit_is_named_not_nameless(seeded_db):
    """Two unrelated account-less findings must not merge into one row
    labelled 'ip None' — each carries the thing it is about."""
    db = seeded_db["db"]
    await db.log_error(source="api", error_type="ValueError",
                       error_msg="Unknown company: ' OR '1'='1",
                       job_name="GET /api/reports/export")
    for i in range(D.T_IPS_PER_EMAIL + 1):
        await db.record_login_attempt(
            user_id=None, email="stuffed@x.com", success=False,
            ip_address=f"10.20.1.{i}", user_agent="curl/8.19.0")

    subjects = {c["subject"] for c in await D.find_candidates(db, hours=24) if c["subject"]}
    assert "GET /api/reports/export" in subjects
    assert "stuffed@x.com" in subjects
