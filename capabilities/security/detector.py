"""Who is probing us — computed from what the platform already recorded.

The 2026-09-08 audit found every signal of the attack already in the
database, read by nobody.  This module is the reader: a set of rules,
each the shape of one thing an attacker does, run over a window, and an
aggregator that turns their hits into a ranked list of candidate
accounts an operator can promote to ``monitored`` with one click.

Two design commitments, both from that incident:

- **It only observes.**  A rule produces a candidate; a human decides.
  A false positive costs a row in a list, never someone's access —
  which is also why ``monitored`` (the action a candidate leads to)
  restricts nothing.
- **It reads the durable tables first.**  ``platform_audit_log``,
  ``login_attempts``, ``password_reset_tokens`` and ``accounts`` held
  the 2026-09-08 evidence and still do, so the detector can be pointed
  at history and must surface that attack from it — the acceptance
  test.  The request ledger (``security_requests``) began filling only
  when the recorder shipped, so the rules that read it (operator-door
  probing, privilege-escalation sweeps) enrich going forward rather
  than backward; they return nothing for a window before the ledger
  existed, which is honest, not broken.

Each rule returns ``Signal``s; ``find_candidates`` groups them by the
account they implicate (falling back to the source IP when a signal has
no account, as the probe's ``/system/*`` attempts did) and ranks the
result by weight.  Nothing here writes.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────

# Registered throwaway-mail providers seen in the probe, plus the
# RFC 2606 reserved names that can only ever be tests or fakes.  A
# short, honest list — not an attempt at a complete disposable-domain
# feed, which belongs to a maintained dataset if we ever want one.
SUSPECT_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamailblock.com",
    "wearehackerone.com", "sharklasers.com", "getnada.com",
    "temp-mail.org", "10minutemail.com", "yopmail.com", "trashmail.com",
    "example.com", "example.net", "example.org", "example.edu", "test.com",
})
RESERVED_TLDS: tuple[str, ...] = (".test", ".example", ".invalid", ".localhost")

# User agents that are not a person's browser.  A signup or login from
# one of these is automation — not proof of malice, but a strong signal
# beside the others.
NON_BROWSER_UA = re.compile(
    r"\b(curl|wget|python-requests|python-httpx|httpx|go-http-client|"
    r"libwww|scrapy|okhttp|java|axios|node-fetch|postmanruntime)\b", re.I)

# Payloads a person does not type into a company-name or vehicle field.
INJECTION_MARKERS = re.compile(
    r"('?\s*or\s+'?1'?\s*=\s*'?1|union\s+select|pg_sleep|sleep\s*\(|"
    r"xp_cmdshell|;\s*drop\s+table|/etc/passwd|\.\./|<script|"
    r"cmd\|'?/c|=cmd\||information_schema)", re.I)

# Thresholds — each set at, or just under, what the 2026-09-08 probe
# actually did, so that run trips every rule and an ordinary customer
# trips none.
T_SIGNUPS_PER_IP = 3        # probe: 33 from one IP in 47 min
T_LOCKOUTS_PER_IP = 10      # probe: 69 account_locked
T_RESETS_PER_USER = 5       # probe: 53 reset tokens for one user in 16 min
T_SYSTEM_403_PER_SRC = 3    # probe: 17 /system/* refusals
T_ADMIN_403_PER_USER = 5    # probe: 25 admin PUT + 6 promote-owner refusals
T_IPS_PER_EMAIL = 5         # probe: 50 IPs for one address in 14 seconds


@dataclass(frozen=True)
class Signal:
    """One rule firing about one subject."""
    rule: str
    severity: str                 # "high" | "med" | "low"
    account_id: int | None
    ip: str | None
    count: int
    evidence: str
    # What this is ABOUT when it is neither an account nor an IP — an
    # email under credential stuffing, an endpoint taking payloads.
    # Without it such hits collapse into one nameless row that tells an
    # operator nothing.
    subject: str | None = None


@dataclass
class Candidate:
    """Everything the rules noticed about one account (or IP)."""
    account_id: int | None
    ip: str | None
    subject: str | None = None
    name: str | None = None
    kind: str | None = None
    signals: list[Signal] = field(default_factory=list)

    @property
    def weight(self) -> int:
        w = {"high": 5, "med": 3, "low": 1}
        return sum(w.get(s.severity, 1) for s in self.signals)

    @property
    def rules(self) -> list[str]:
        # stable, de-duplicated, in first-seen order
        out: list[str] = []
        for s in self.signals:
            if s.rule not in out:
                out.append(s.rule)
        return out


def _cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=int(hours))).isoformat()


def _is_public_ip(value: str | None) -> bool:
    """True only for an address a client could not have invented.

    Private, loopback, link-local, multicast and the reserved
    documentation ranges are all values a spoofed header can carry and
    a real internet client never has.
    """
    if not value:
        return False
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    if not addr.is_global or addr.is_private or addr.is_loopback:
        return False
    # 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 (RFC 5737) and
    # 2001:db8::/32 (RFC 3849) are documentation-only.
    doc = (ipaddress.ip_network("192.0.2.0/24"),
           ipaddress.ip_network("198.51.100.0/24"),
           ipaddress.ip_network("203.0.113.0/24"),
           ipaddress.ip_network("2001:db8::/32"))
    return not any(addr in n for n in doc if addr.version == n.version)


def _suspect_domain(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain in SUSPECT_EMAIL_DOMAINS or domain.endswith(RESERVED_TLDS)


# ── rules — each reads one durable source, returns Signals ────────

async def rule_signup_burst(db, hours: int) -> list[Signal]:
    """Many accounts created from one IP in the window (platform_audit_log)."""
    cur = await db.execute(
        "SELECT account_id, details FROM platform_audit_log "
        "WHERE event = 'account_created' AND created_at >= ?",
        (_cutoff(hours),))
    rows = await cur.fetchall()
    by_ip: dict[str, list[int]] = {}
    for r in rows:
        m = re.search(r"ip=([^\s]+)", r["details"] or "")
        ip = m.group(1) if m else "?"
        by_ip.setdefault(ip, []).append(r["account_id"])
    out: list[Signal] = []
    for ip, accts in by_ip.items():
        if ip != "?" and len(accts) > T_SIGNUPS_PER_IP:
            # one signal per account so the candidate list names them all
            for aid in accts:
                out.append(Signal(
                    rule="signup_burst", severity="high", account_id=aid, ip=ip,
                    count=len(accts),
                    evidence=f"{len(accts)} accounts created from {ip} in {hours}h"))
    return out


async def rule_disposable_email(db, hours: int) -> list[Signal]:
    """Owner signed up with a throwaway / reserved domain (accounts+users)."""
    cur = await db.execute(
        "SELECT a.id AS account_id, u.email FROM accounts a "
        "JOIN users u ON u.account_id = a.id AND u.is_primary_owner = 1 "
        "WHERE a.created_at >= ?",
        (_cutoff(hours),))
    out: list[Signal] = []
    for r in await cur.fetchall():
        if _suspect_domain(r["email"]):
            out.append(Signal(
                rule="disposable_email", severity="med",
                account_id=r["account_id"], ip=None, count=1,
                evidence=f"owner email {r['email']}"))
    return out


async def rule_lockout_storm(db, hours: int) -> list[Signal]:
    """Repeated account_locked from one IP — brute force (login_attempts)."""
    cur = await db.execute(
        "SELECT ip_address, COUNT(*) AS n FROM login_attempts "
        "WHERE failure_reason IN ('account_locked', 'lockout_triggered', 'bad_password') "
        "AND attempted_at >= ? GROUP BY ip_address HAVING COUNT(*) > ?",
        (_cutoff(hours), T_LOCKOUTS_PER_IP))
    return [Signal(rule="lockout_storm", severity="high", account_id=None,
                   ip=r["ip_address"], count=int(r["n"]),
                   evidence=f"{r['n']} failed/locked logins from {r['ip_address']}")
            for r in await cur.fetchall()]


async def rule_non_browser_auth(db, hours: int) -> list[Signal]:
    """Auth traffic from a non-browser user agent (login_attempts).

    Deliberately ``low``: a curl login is true of our own smoke tests and
    of any integration, and on 2026-09-10 it produced seventy-two
    near-identical rows because each spoofed IP became its own subject.
    A low signal never opens a candidate on its own — it only sharpens
    one another rule already opened (see ``find_candidates``).
    """
    cur = await db.execute(
        "SELECT ip_address, user_agent, COUNT(*) AS n FROM login_attempts "
        "WHERE attempted_at >= ? AND user_agent IS NOT NULL "
        "GROUP BY ip_address, user_agent",
        (_cutoff(hours),))
    out: list[Signal] = []
    for r in await cur.fetchall():
        if NON_BROWSER_UA.search(r["user_agent"] or ""):
            out.append(Signal(
                rule="non_browser_auth", severity="low", account_id=None,
                ip=r["ip_address"], count=int(r["n"]),
                evidence=f"{r['n']} auth calls as {r['user_agent'][:60]!r} from {r['ip_address']}"))
    return out


async def rule_ip_rotation(db, hours: int) -> list[Signal]:
    """One email tried from many PUBLIC IPs — credential stuffing.

    Counts only addresses a client cannot invent.  On 2026-09-10 one
    address was tried from seventy distinct values in fourteen seconds
    and every one was fabricated — fifty inside 10.20.0.0/24, twenty
    more as 1.2.3.x — because ``X-Forwarded-For`` was still trusted as
    the client wrote it.  A rule that counted those would have been
    counting the attacker's own input: evade it by using five values,
    or weaponise it by spoofing fifty against a real person's address
    to have the platform flag its own owner.

    Since the realip fix nginx sends a single true value and Cloudflare
    supplies it, so a genuine client IP is always public; anything
    private, loopback, link-local or reserved-for-documentation is a
    leftover of the spoofable era or a probe still trying. Filtering
    them is what makes the count mean machines again.
    """
    cur = await db.execute(
        "SELECT email, ip_address, COUNT(*) AS n FROM login_attempts "
        "WHERE attempted_at >= ? AND email IS NOT NULL AND ip_address IS NOT NULL "
        "GROUP BY email, ip_address",
        (_cutoff(hours),))
    per_email: dict[str, tuple[set[str], int]] = {}
    for r in await cur.fetchall():
        if not _is_public_ip(r["ip_address"]):
            continue
        ips, n = per_email.get(r["email"], (set(), 0))
        ips.add(r["ip_address"])
        per_email[r["email"]] = (ips, n + int(r["n"]))
    return [
        Signal(rule="ip_rotation", severity="high", account_id=None, ip=None,
               count=len(ips), subject=email,
               evidence=f"tried from {len(ips)} public IPs ({n} attempts)")
        for email, (ips, n) in per_email.items()
        if len(ips) > T_IPS_PER_EMAIL
    ]


async def rule_reset_flood(db, hours: int) -> list[Signal]:
    """Many password-reset tokens for one user (password_reset_tokens)."""
    cur = await db.execute(
        "SELECT u.account_id, u.email, COUNT(*) AS n "
        "FROM password_reset_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE t.created_at >= ? GROUP BY u.account_id, u.email HAVING COUNT(*) > ?",
        (_cutoff(hours), T_RESETS_PER_USER))
    return [Signal(rule="reset_flood", severity="med", account_id=r["account_id"],
                   ip=None, count=int(r["n"]),
                   evidence=f"{r['n']} password-reset tokens for {r['email']}")
            for r in await cur.fetchall()]


async def rule_injection_attempt(db, hours: int) -> list[Signal]:
    """Injection payloads in captured errors (error_log)."""
    cur = await db.execute(
        "SELECT account_id, job_name, error_msg, COUNT(*) AS n FROM error_log "
        "WHERE created_at >= ? GROUP BY account_id, job_name, error_msg",
        (_cutoff(hours),))
    out: list[Signal] = []
    for r in await cur.fetchall():
        if INJECTION_MARKERS.search(r["error_msg"] or ""):
            out.append(Signal(
                rule="injection_attempt", severity="high",
                account_id=r["account_id"], ip=None, count=int(r["n"]),
                subject=r["job_name"] or "unattributed",
                evidence=f"payload reached {r['job_name'] or '?'}: {r['error_msg'][:70]}"))
    return out


async def rule_operator_probing(db, hours: int) -> list[Signal]:
    """Refused hits on the operator surface (security_requests, ledger-only)."""
    cur = await db.execute(
        "SELECT account_id, ip, COUNT(*) AS n FROM security_requests "
        "WHERE created_at >= ? AND status IN (401, 403) AND path LIKE '/api/system/%' "
        "GROUP BY account_id, ip HAVING COUNT(*) > ?",
        (_cutoff(hours), T_SYSTEM_403_PER_SRC))
    return [Signal(rule="operator_probing", severity="high",
                   account_id=r["account_id"], ip=r["ip"], count=int(r["n"]),
                   evidence=f"{r['n']} refused /system/* attempts")
            for r in await cur.fetchall()]


async def rule_privilege_sweep(db, hours: int) -> list[Signal]:
    """Refused admin writes — escalation attempts (security_requests, ledger-only)."""
    cur = await db.execute(
        "SELECT account_id, ip, COUNT(*) AS n FROM security_requests "
        "WHERE created_at >= ? AND status = 403 AND method IN ('PUT','POST','PATCH','DELETE') "
        "AND path LIKE '/api/admin/%' GROUP BY account_id, ip HAVING COUNT(*) > ?",
        (_cutoff(hours), T_ADMIN_403_PER_USER))
    return [Signal(rule="privilege_sweep", severity="high",
                   account_id=r["account_id"], ip=r["ip"], count=int(r["n"]),
                   evidence=f"{r['n']} refused admin writes")
            for r in await cur.fetchall()]


ALL_RULES = (
    rule_signup_burst, rule_disposable_email, rule_lockout_storm,
    rule_non_browser_auth, rule_ip_rotation, rule_reset_flood,
    rule_injection_attempt, rule_operator_probing, rule_privilege_sweep,
)


async def find_candidates(db, *, hours: int = 24 * 7) -> list[dict]:
    """Run every rule, group hits into ranked candidates.

    Grouped by account when the signal names one; otherwise by IP, so a
    refusal storm with no valid account (the probe's /system/* attempts)
    still surfaces as a candidate an operator can look at.  Already
    monitored/quarantined accounts are annotated, not hidden — seeing
    that a rule still fires on a watched account is the point.
    """
    # Accept either the Database or the raw handle its mixins use, so a
    # caller can hand over ``platform_db`` without reaching into it.
    handle = getattr(db, "_db", db)
    signals: list[Signal] = []
    failed: list[str] = []
    for rule in ALL_RULES:
        try:
            signals.extend(await rule(handle, hours))
        except Exception as e:  # noqa: BLE001 — one broken rule must not blind the rest
            # ...but a rule that fails SILENTLY is how "nothing suspicious"
            # and "the detector is broken" become the same output.  The
            # first run of this module returned zero candidates because
            # all eight rules were raising against a mistyped handle, and
            # the swallow made that look like a clean result.
            failed.append(rule.__name__)
            logger.warning("security detector: rule %s failed: %s", rule.__name__, e)
    if failed and len(failed) == len(ALL_RULES):
        logger.error(
            "security detector: EVERY rule failed (%s) — the empty result "
            "means broken, not clean", ", ".join(failed))

    # A candidate is opened only by a signal that means something on its
    # own (med or high).  Low signals attach to a subject already
    # implicated — otherwise "someone used curl" fills the page with rows
    # an operator has no reason to read, which is how a detector stops
    # being read at all.
    cands: dict[tuple, Candidate] = {}
    def _key(sig: Signal) -> tuple:
        if sig.account_id is not None:
            return ("acct", sig.account_id)
        if sig.ip:
            return ("ip", sig.ip)
        return ("subject", sig.subject or "unattributed")

    for s in (x for x in signals if x.severity != "low"):
        key = _key(s)
        c = cands.get(key)
        if c is None:
            c = Candidate(account_id=s.account_id, ip=s.ip, subject=s.subject)
            cands[key] = c
        c.signals.append(s)
    for s in (x for x in signals if x.severity == "low"):
        key = _key(s)
        if key in cands:
            cands[key].signals.append(s)

    # annotate the account-keyed candidates with name + current kind
    acct_ids = [c.account_id for c in cands.values() if c.account_id is not None]
    if acct_ids:
        placeholders = ",".join("?" * len(acct_ids))
        cur = await handle.execute(
            f"SELECT id, name, kind FROM accounts WHERE id IN ({placeholders})",
            acct_ids)
        meta = {r["id"]: (r["name"], r["kind"]) for r in await cur.fetchall()}
        for c in cands.values():
            if c.account_id in meta:
                c.name, c.kind = meta[c.account_id]
        # An account we have already classified as ours is not a finding:
        # the rules describe our own fixtures exactly as well as they
        # describe a stranger, so without this the page shows the same
        # test accounts forever and stops being read.  ``monitored``
        # deliberately STAYS — seeing that a rule still fires on someone
        # we are watching is the entire point of watching them.
        cands = {k: c for k, c in cands.items() if c.kind != "test"}

    ranked = sorted(cands.values(), key=lambda c: (c.weight, len(c.signals)), reverse=True)
    return [_as_dict(c) for c in ranked]


def _as_dict(c: Candidate) -> dict[str, Any]:
    return {
        "account_id": c.account_id,
        "ip": c.ip,
        "subject": c.subject,
        "name": c.name,
        "kind": c.kind,
        "weight": c.weight,
        "rules": c.rules,
        "signals": [
            {"rule": s.rule, "severity": s.severity, "count": s.count, "evidence": s.evidence}
            for s in c.signals
        ],
    }
