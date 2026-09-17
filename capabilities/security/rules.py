"""What the detector looks for — each rule is one thing the 2026-09-08 probe did.

The audit of that day found every signal already sitting in the
database with nothing reading it.  Each rule below reads one of those
signals and names the behaviour it saw:

  signup_burst_ip     33 accounts in 47 minutes from one address; 10 in 24s
  disposable_signup   guerrillamailblock, mailinator, wearehackerone, example.com
  lockout_storm       69 × account_locked against two accounts
  nonbrowser_auth     curl/8.19.0 on every login
  injection_payload   ' OR '1'='1 · pg_sleep(5) · UNION SELECT · =cmd|'/C calc
  operator_gate_probe 17 × /api/system/* refused
  privilege_sweep     25 × PUT /api/admin/* refused, 6 × promote-owner
  reset_flood         53 password-reset tokens for one user in 16 minutes

Rules are pure: they take rows the storage layer already grouped and
return Findings.  Thresholds are here, in one place, with the number
from the day beside each so the next person can see why the line was
drawn where it was.

Observation only.  A Finding is a row on the operator's Security page
and, when high, a message to the owners — never a block.  That is what
makes the thresholds safe to keep tight: a false positive costs a
glance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# ── thresholds (the 09-08 number is in the comment) ───────────────

SIGNUPS_PER_IP = 3            # 33/47min; 10 in 24s
LOCKOUTS_PER_IP = 10          # 69
NONBROWSER_ATTEMPTS = 3       # every attempt that day
SYSTEM_REFUSALS_PER_IP = 3    # 17
ADMIN_REFUSALS_PER_SUBJECT = 5  # 25 PUTs + 6 promote-owner
RESET_TOKENS_PER_USER = 5     # 53 / 16 min

# Domains that exist to be thrown away, plus the RFC 2606 reserved ones
# that cannot receive mail at all — a signup with one of these has no
# intention of being reached.
DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    "guerrillamail.com", "guerrillamailblock.com", "guerrillamail.net",
    "mailinator.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "yopmail.com", "sharklasers.com", "trashmail.com", "dispostable.com",
    "getnada.com", "mohmal.com", "maildrop.cc", "throwawaymail.com",
    "wearehackerone.com",
    "example.com", "example.net", "example.org",
})
RESERVED_TLDS: tuple[str, ...] = (".invalid", ".test", ".example", ".localhost")

NONBROWSER_UA = re.compile(
    r"^(curl|wget|python-requests|python-urllib|httpx|aiohttp|go-http-client|"
    r"java|okhttp|libwww-perl|scrapy|node-fetch|axios|postman)\b", re.I)

INJECTION = re.compile(
    r"(\bunion\b\s+\bselect\b|\bpg_sleep\s*\(|\bor\s+'?1'?\s*=\s*'?1|--\s*$|;\s*(drop|select|insert|update)\b|"
    r"<script\b|=cmd\||=\s*hyperlink\(|\.\./\.\./|/etc/passwd)", re.I)


@dataclass
class Finding:
    kind: str
    severity: str               # high | medium | low
    subject_type: str           # ip | account | user | email | request
    subject: str
    summary: str
    count: int
    seen_at: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def is_disposable(email: str) -> bool:
    d = _domain(email)
    return bool(d) and (d in DISPOSABLE_DOMAINS or d.endswith(RESERVED_TLDS))


# ── the rules ─────────────────────────────────────────────────────

async def signup_burst_ip(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_signups(since_hours=since_hours)
    by_ip: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("ip"):
            by_ip.setdefault(r["ip"], []).append(r)
    out = []
    for ip, sign in by_ip.items():
        if len(sign) >= SIGNUPS_PER_IP:
            out.append(Finding(
                kind="signup_burst_ip", severity="high", subject_type="ip", subject=ip,
                summary=f"{len(sign)} accounts created from {ip} in {since_hours}h",
                count=len(sign), seen_at=max(s["created_at"] for s in sign),
                evidence={"accounts": [s["account_id"] for s in sign][:50],
                          "names": [s["name"] for s in sign if s.get("name")][:10]},
            ))
    return out


async def disposable_signup(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_new_users(since_hours=since_hours)
    out = []
    for r in rows:
        if is_disposable(r["email"]):
            out.append(Finding(
                kind="disposable_signup", severity="medium", subject_type="email", subject=r["email"],
                summary=f"signup with a throwaway address ({_domain(r['email'])}) on account {r['account_id']}",
                count=1, seen_at=r["created_at"],
                evidence={"account_id": r["account_id"], "user_id": r["id"], "domain": _domain(r["email"])},
            ))
    return out


async def lockout_storm(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_login_failures(since_hours=since_hours)
    by_ip: dict[str, int] = {}
    last: dict[str, str] = {}
    for r in rows:
        if r.get("failure_reason") in ("lockout_triggered", "account_locked") and r.get("ip"):
            by_ip[r["ip"]] = by_ip.get(r["ip"], 0) + int(r["n"])
            last[r["ip"]] = max(last.get(r["ip"], ""), r.get("last_at") or "")
    return [Finding(
        kind="lockout_storm", severity="high", subject_type="ip", subject=ip,
        summary=f"{n} locked-out login attempts from {ip} in {since_hours}h",
        count=n, seen_at=last[ip], evidence={"locked_attempts": n},
    ) for ip, n in by_ip.items() if n >= LOCKOUTS_PER_IP]


async def nonbrowser_auth(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_auth_user_agents(since_hours=since_hours)
    by_ip: dict[str, dict] = {}
    for r in rows:
        ua = r.get("ua") or ""
        if r.get("ip") and NONBROWSER_UA.search(ua):
            e = by_ip.setdefault(r["ip"], {"n": 0, "uas": set(), "last": ""})
            e["n"] += int(r["n"]); e["uas"].add(ua[:60]); e["last"] = max(e["last"], r.get("last_at") or "")
    return [Finding(
        kind="nonbrowser_auth", severity="low", subject_type="ip", subject=ip,
        summary=f"{e['n']} sign-in attempts from a script ({', '.join(sorted(e['uas']))[:80]}) at {ip}",
        count=e["n"], seen_at=e["last"], evidence={"user_agents": sorted(e["uas"])},
    ) for ip, e in by_ip.items() if e["n"] >= NONBROWSER_ATTEMPTS]


async def injection_payload(db, since_hours: int) -> list[Finding]:
    out = []
    for r in await db.security_signal_error_messages(since_hours=since_hours):
        msg = r.get("error_msg") or ""
        if INJECTION.search(msg):
            out.append(Finding(
                kind="injection_payload", severity="high", subject_type="request",
                subject=f"{r.get('job_name') or 'api'}#{r['id']}",
                summary=f"injection-shaped input reached {r.get('job_name') or 'the API'}: {msg[:80]}",
                count=1, seen_at=r["created_at"],
                evidence={"error_log_id": r["id"], "message": msg[:200], "account_id": r.get("account_id")},
            ))
    for r in await db.security_signal_request_queries(since_hours=since_hours):
        q = r.get("query") or ""
        if INJECTION.search(q):
            out.append(Finding(
                kind="injection_payload", severity="high", subject_type="ip", subject=r.get("ip") or "unknown",
                summary=f"injection-shaped query on {r['method']} {r['path']}: {q[:80]}",
                count=1, seen_at=r["created_at"],
                evidence={"path": r["path"], "query": q[:200], "account_id": r.get("account_id")},
            ))
    return out


async def operator_gate_probe(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_refusals(since_hours=since_hours)
    by_ip: dict[str, int] = {}
    last: dict[str, str] = {}
    for r in rows:
        if r.get("ip") and int(r.get("system_refusals") or 0):
            by_ip[r["ip"]] = by_ip.get(r["ip"], 0) + int(r["system_refusals"])
            last[r["ip"]] = max(last.get(r["ip"], ""), r.get("last_at") or "")
    return [Finding(
        kind="operator_gate_probe", severity="high", subject_type="ip", subject=ip,
        summary=f"{n} refused attempts on the operator console (/api/system/*) from {ip}",
        count=n, seen_at=last[ip], evidence={"system_refusals": n},
    ) for ip, n in by_ip.items() if n >= SYSTEM_REFUSALS_PER_IP]


async def privilege_sweep(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_refusals(since_hours=since_hours)
    by_subject: dict[tuple[str, str], int] = {}
    last: dict[tuple[str, str], str] = {}
    for r in rows:
        n = int(r.get("admin_refusals") or 0)
        if not n:
            continue
        key = ("account", str(r["account_id"])) if r.get("account_id") else ("ip", r.get("ip") or "unknown")
        by_subject[key] = by_subject.get(key, 0) + n
        last[key] = max(last.get(key, ""), r.get("last_at") or "")
    return [Finding(
        kind="privilege_sweep", severity="high", subject_type=st, subject=subj,
        summary=f"{n} refused admin actions (/api/admin/*) by {st} {subj}",
        count=n, seen_at=last[(st, subj)], evidence={"admin_refusals": n},
    ) for (st, subj), n in by_subject.items() if n >= ADMIN_REFUSALS_PER_SUBJECT]


async def reset_flood(db, since_hours: int) -> list[Finding]:
    rows = await db.security_signal_reset_tokens(since_hours=since_hours)
    return [Finding(
        kind="reset_flood", severity="medium", subject_type="user", subject=str(r["user_id"]),
        summary=f"{int(r['n'])} password-reset tokens for {r.get('email') or f'user {r['user_id']}'} in {since_hours}h",
        count=int(r["n"]), seen_at=r.get("last_at") or "",
        evidence={"email": r.get("email"), "account_id": r.get("account_id")},
    ) for r in rows if int(r["n"]) >= RESET_TOKENS_PER_USER]


Rule = Callable[[Any, int], Awaitable[list[Finding]]]

RULES: tuple[Rule, ...] = (
    signup_burst_ip, disposable_signup, lockout_storm, nonbrowser_auth,
    injection_payload, operator_gate_probe, privilege_sweep, reset_flood,
)


async def run_rules(db, since_hours: int = 1) -> list[Finding]:
    """Every rule over the window; one rule's failure never hides another's."""
    import logging
    log = logging.getLogger(__name__)
    out: list[Finding] = []
    for rule in RULES:
        try:
            out.extend(await rule(db, since_hours))
        except Exception:  # noqa: BLE001 — a broken rule is logged, the sweep continues
            log.exception("security rule %s failed", rule.__name__)
    return out
