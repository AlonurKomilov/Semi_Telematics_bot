"""The detector running unattended: what it wakes someone for, what it
refuses to, and the one thing it must never do — promote.

And the owner notice: built, off, and the shapes it will not send even
when on.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.security import watch as W
from capabilities.security import owner_notice as N



def _run(cands, failed=(), total=9):
    """A DetectorRun the way run_detector would hand it back."""
    from capabilities.security.detector import DetectorRun
    return DetectorRun(candidates=list(cands), failed_rules=tuple(failed),
                       total_rules=total)


def _patch_detector(monkeypatch, cands, failed=(), total=9):
    async def fake(db_, *, hours):
        assert hours == W.WINDOW_HOURS
        return _run(cands, failed, total)
    monkeypatch.setattr("capabilities.security.detector.run_detector", fake)


def _cand(**over) -> dict:
    base = {
        "account_id": 42, "ip": None, "subject": "acct:42",
        "name": "Attacker Corp", "kind": "test", "security": "normal",
        "people": [], "severity": "high", "weight": 9,
        "rules": ["signup_burst", "operator_door"],
        "signals": [],
    }
    base.update(over)
    return base


# ── what is worth waking somebody for ─────────────────────────────

def test_two_high_rules_on_a_fresh_subject_is_worth_a_message():
    assert W.worth_waking_for(_cand()) is True


def test_one_rule_is_an_argument_not_a_case():
    """The console calls a candidate an argument, not a verdict. One
    rule firing is exactly one argument."""
    assert W.worth_waking_for(_cand(rules=["operator_door"])) is False


@pytest.mark.parametrize("sev", ["med", "low"])
def test_only_high_wakes_anyone(sev):
    assert W.worth_waking_for(_cand(severity=sev)) is False


@pytest.mark.parametrize("standing", ["monitored", "quarantined"])
def test_a_subject_somebody_already_decided_about_is_not_news(standing):
    """Every request from it is already being recorded. Waking the
    operator again to say so is noise, and noise is how a real alert
    gets muted."""
    assert W.worth_waking_for(_cand(security=standing)) is False


# ── the message ───────────────────────────────────────────────────

def test_nothing_worth_it_means_no_message_at_all():
    assert W.compose([_cand(severity="med"), _cand(rules=["x"])]) is None
    assert W.compose([]) is None


def test_the_message_names_who_and_which_rules_and_never_the_evidence():
    text = W.compose([_cand(signals=[{"rule": "injection", "severity": "high",
                                      "count": 3,
                                      "evidence": "' OR 1=1 --"}])])
    assert text is not None
    assert "Attacker Corp" in text
    assert "signup_burst" in text and "operator_door" in text
    assert "OR 1=1" not in text, "a payload in a Telegram message is a payload"
    assert "system.4truck.us/security" in text


def test_the_message_says_nothing_was_changed():
    """The reader must not be left thinking the platform acted. It
    did not, and that is the design."""
    text = W.compose([_cand()])
    assert "Nothing was changed" in text
    assert "Recording starts only" in text


def test_html_in_a_name_is_escaped():
    text = W.compose([_cand(name="<b>Evil</b> & Co")])
    assert "<b>Evil</b>" not in text
    assert "&lt;b&gt;Evil&lt;/b&gt; &amp; Co" in text


def test_too_many_in_one_night_is_reported_as_a_broken_rule_not_listed():
    """Nine subjects in one night is not nine attackers. The number IS
    the finding, and a list of nine is a list nobody reads."""
    many = [_cand(account_id=i, name=f"Acct {i}") for i in range(W.TOO_MANY + 1)]
    text = W.compose(many)
    assert str(W.TOO_MANY + 1) in text
    assert "rule describing" in text
    assert "Acct 3" not in text, "not listed"
    assert "Nothing was changed" in text


def test_a_detector_that_could_not_run_is_said_loudly():
    """The empty result means broken, not clean — the detector's own
    rule. A night it did not run is a night nobody was watching."""
    text = W.compose([], failed=True)
    assert text is not None
    assert "could not run" in text
    assert "broken, not clean" in text


# ── the run: it wakes, and it promotes NOTHING ────────────────────

class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _App:
    def __init__(self):
        self.bot = _Bot()


@pytest.mark.asyncio
async def test_the_run_tells_every_operator_and_touches_no_standing(monkeypatch, seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]

    _patch_detector(monkeypatch, [_cand(account_id=acct.id, name=acct.name, kind="real")])
    from capabilities.permissions import roles
    monkeypatch.setattr(roles, "SYSTEM_OWNER_IDS", {111, 222}, raising=False)

    app = _App()
    res = await W.run(db, app)
    assert res["woke_for"] == 1
    assert res["sent_to"] == 2
    assert sorted(c for c, _ in app.bot.sent) == [111, 222]
    assert acct.name in app.bot.sent[0][1]

    # The one thing this must never do.
    fresh = await db.get_account(acct.id)
    assert (fresh.security or "normal") == "normal", (
        "the watch woke someone; it did not decide for them"
    )


@pytest.mark.asyncio
async def test_a_quiet_night_sends_nothing(monkeypatch, seeded_db):
    db = seeded_db["db"]

    _patch_detector(monkeypatch, [_cand(severity="low")])
    app = _App()
    res = await W.run(db, app)
    assert res["message"] is None and app.bot.sent == []


@pytest.mark.asyncio
async def test_a_broken_detector_still_reaches_the_operator(monkeypatch, seeded_db):
    db = seeded_db["db"]

    async def boom(db_, *, hours):
        raise RuntimeError("rules table gone")
    monkeypatch.setattr("capabilities.security.detector.run_detector", boom)
    from capabilities.permissions import roles
    monkeypatch.setattr(roles, "SYSTEM_OWNER_IDS", {111}, raising=False)
    app = _App()
    res = await W.run(db, app)
    assert res["sent_to"] == 1
    assert "could not run" in app.bot.sent[0][1]


@pytest.mark.asyncio
async def test_no_operator_configured_is_logged_not_raised(monkeypatch, seeded_db):
    db = seeded_db["db"]

    _patch_detector(monkeypatch, [_cand()])
    from capabilities.permissions import roles
    monkeypatch.setattr(roles, "SYSTEM_OWNER_IDS", set(), raising=False)
    monkeypatch.setattr("infra.bot_registry.get_system_app", lambda: None, raising=False)
    res = await W.run(db, None)
    assert res["woke_for"] == 1 and res["sent_to"] == 0


def test_the_job_is_registered_and_catalogued():
    """A job the console cannot label is a job nobody can see run."""
    from tests._repo import REPO
    src = (REPO / "interfaces" / "bot" / "scheduler.py").read_text()
    assert 'id="security_watch_nightly"' in src
    assert '"security_watch_nightly":' in src, "missing from _JOB_META"


# ── the owner notice: built, OFF, and what it will never send ─────

async def _owner_and_person(db, acct):
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    from adapters.storage import Role
    person = await db.create_user_with_email(
        email="aziz@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Aziz")
    return owner, person


def _capture(monkeypatch):
    sent: list = []

    async def fake_notify(db_, account_id, user_id, content, **kw):
        sent.append((account_id, user_id, content))
        return []
    monkeypatch.setattr("capabilities.notifications.service.notify_user", fake_notify)
    return sent


@pytest.mark.asyncio
async def test_the_owner_notice_is_off_by_default(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.delenv(N.ENV_KEY, raising=False)
    owner, person = await _owner_and_person(db, acct)
    sent = _capture(monkeypatch)
    assert await N.enabled(db) is False
    assert await N.tell_owner(db, account_id=acct.id, person=person) is False
    assert sent == []


@pytest.mark.asyncio
async def test_when_on_the_owner_is_told_about_a_person_not_told_why(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setenv(N.ENV_KEY, "1")
    owner, person = await _owner_and_person(db, acct)
    sent = _capture(monkeypatch)

    assert await N.tell_owner(db, account_id=acct.id, person=person) is True
    assert len(sent) == 1
    account_id, target, content = sent[0]
    assert (account_id, target) == (acct.id, owner.id)
    said = f"{content.title}\n{content.body}"
    assert "Aziz" in said
    assert "Nothing is blocked" in said
    assert "Team Management" in said, "tells them what THEY can do"
    lowered = said.lower()
    for word in ("suspicious", "attack", "probing", "breach", "malicious",
                 "fraud", "hacked", "compromised"):
        assert word not in lowered, f"{word!r} states a finding we do not have"


@pytest.mark.asyncio
async def test_the_console_switch_works_without_the_env(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.delenv(N.ENV_KEY, raising=False)
    await db.set_platform_setting(N.SETTING_KEY, "1")
    owner, person = await _owner_and_person(db, acct)
    sent = _capture(monkeypatch)
    assert await N.tell_owner(db, account_id=acct.id, person=person) is True
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_never_for_a_test_account(seeded_db, monkeypatch):
    """There is no customer behind a test account to tell — and the
    09-08 probe's thirty-three were all test."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setenv(N.ENV_KEY, "1")
    owner, person = await _owner_and_person(db, acct)
    await db.update_account(acct.id, kind="test")
    sent = _capture(monkeypatch)
    assert await N.tell_owner(db, account_id=acct.id, person=person) is False
    assert sent == []


@pytest.mark.asyncio
async def test_never_when_the_watched_login_is_the_owners_own(seeded_db, monkeypatch):
    """That is the login most likely to be in the wrong hands. Telling
    it tells them."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setenv(N.ENV_KEY, "1")
    owner, _ = await _owner_and_person(db, acct)
    fresh_owner = await db.get_user_by_id(owner.id)
    sent = _capture(monkeypatch)
    assert await N.tell_owner(db, account_id=acct.id, person=fresh_owner) is False
    assert sent == []


@pytest.mark.asyncio
async def test_a_notice_that_fails_never_raises(seeded_db, monkeypatch):
    class Broken:
        async def get_platform_setting(self, key, default=""):
            return "1"
        async def get_account(self, _):
            raise RuntimeError("down")
    monkeypatch.delenv(N.ENV_KEY, raising=False)
    person = type("P", (), {"id": 5, "display_name": "x"})()
    assert await N.tell_owner(Broken(), account_id=1, person=person) is False


@pytest.mark.asyncio
async def test_the_operator_click_to_monitored_reaches_the_owner_when_on(
        pg_db, seeded_db, monkeypatch):
    """End to end: the console's PATCH is what fires it — a human's
    decision, signed in the audit trail — never the detector."""
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: seeded_db["db"])
    monkeypatch.setenv(N.ENV_KEY, "1")
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner, person = await _owner_and_person(db, acct)
    sent = _capture(monkeypatch)

    from interfaces.api.app import create_api
    from interfaces.api.deps import require_system_owner
    app = create_api()
    app.dependency_overrides[require_system_owner] = lambda: {"sub": "1", "role": "owner"}
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/api/system/users/{person.id}/security",
                          json={"security": "monitored"})
    assert r.status_code == 200, r.text
    assert r.json()["owner_told"] is True
    assert len(sent) == 1 and sent[0][1] == owner.id


# ── what the detector could NOT do reaches the operator too ───────
#
# The detector swallows each failing rule so one cannot blind the rest,
# and returns an EMPTY list when every rule failed. The first version of
# this job only knew a failure that raised — so the one failure mode the
# "could not run" message exists for was the one it could never see. The
# review that caught it reproduced it with a handle whose every query
# raises; so does the test below, through the real detector.

class _BrokenHandle:
    """Every query raises — the detector's own first-run failure."""
    async def execute(self, *a, **k):
        raise RuntimeError("relation does not exist")


@pytest.mark.asyncio
async def test_every_rule_failing_reaches_the_operator_through_the_real_detector(monkeypatch):
    from capabilities.permissions import roles
    monkeypatch.setattr(roles, "SYSTEM_OWNER_IDS", {111}, raising=False)
    app = _App()
    res = await W.run(_BrokenHandle(), app)
    assert res["checked"] == 0
    assert len(res["failed_rules"]) == 9, res["failed_rules"]
    assert res["sent_to"] == 1, "an empty result from a broken detector is NOT a quiet night"
    assert "could not run" in app.bot.sent[0][1]
    assert "broken, not clean" in app.bot.sent[0][1]


def test_broken_is_read_from_the_run_not_only_from_a_raise():
    from capabilities.security.detector import RULES
    text = W.compose([], failed=False, failed_rules=tuple(RULES), total_rules=len(RULES))
    assert text is not None and "could not run" in text


def test_a_partial_failure_is_a_footer_on_a_real_finding():
    """One rule's query breaks after a schema change; the other eight
    keep running. The finding is real AND the list is incomplete, and
    the reader must be told both."""
    text = W.compose([_cand()], failed_rules=("ip_rotation", "reset_flood"), total_rules=9)
    assert "Attacker Corp" in text
    assert "2 of 9 rules did not run: ip_rotation, reset_flood" in text
    assert "This list is incomplete" in text
    assert "Nothing was changed" in text


def test_a_partial_failure_with_nothing_found_is_still_a_message():
    """Nine rules, eight ran, none fired. 'Nothing found' is only true
    of the eight — and a rule silently dead for months is exactly what
    this exists to prevent. It nags every night it stays true."""
    text = W.compose([], failed_rules=("ip_rotation",), total_rules=9)
    assert text is not None
    assert "incomplete" in text
    assert "1 of 9 rules did not run: ip_rotation" in text
    assert "not all of it" in text


def test_a_failed_rule_id_in_the_message_is_escaped():
    text = W.compose([], failed_rules=("<script>",), total_rules=9)
    assert "<script>" not in text and "&lt;script&gt;" in text


@pytest.mark.asyncio
async def test_the_run_reports_partial_failure(monkeypatch, seeded_db):
    db = seeded_db["db"]
    _patch_detector(monkeypatch, [_cand()], failed=("ip_rotation",), total=9)
    from capabilities.permissions import roles
    monkeypatch.setattr(roles, "SYSTEM_OWNER_IDS", {111}, raising=False)
    app = _App()
    res = await W.run(db, app)
    assert res["failed_rules"] == ["ip_rotation"]
    assert "1 of 9 rules did not run" in app.bot.sent[0][1]
