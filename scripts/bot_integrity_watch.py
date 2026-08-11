#!/usr/bin/env python3
"""Bot integrity watcher — detects tampering we cannot prevent.

Born in the 2026-08-11 incident: the owner's Telegram account was taken
over and, while locked out of BotFather, the attacker could at any
moment re-deface a bot, re-point its webhook, revoke its token, or
TRANSFER OWNERSHIP away.  None of that is preventable from this server
— but all of it is *detectable within minutes*, which is the
difference between "we have evidence and can act" and "the bot died
last week and nobody knows why".

Every run fingerprints each bot we own:

  * identity        — getMe (a 401 here means the token was REVOKED)
  * webhook         — getWebhookInfo (an attacker-set URL steals every
                      user message; this is exactly what happened to
                      @kurs_uzbekistan_bot)
  * menu button     — getChatMenuButton (mini-app URL swaps)
  * name / desc     — per LANGUAGE CODE, because the casino text was
                      hidden in the ``en`` variant while the default
                      read clean
  * commands        — getMyCommands (the "BEST CASINO MINI-APP" entry)

…and the watched Telegram account's public profile (getChat: name,
username, bio) so a recovery — or a further defacement — is timestamped.

Any change from the stored baseline is sent to SYSTEM_OWNER_IDS via the
system bot.  Read-only against Telegram; sends nothing to anyone but
the operator.  Tokens are NEVER written to the state file (bots are
keyed by username).

Usage:
    python3 scripts/bot_integrity_watch.py            # check + alert
    python3 scripts/bot_integrity_watch.py --baseline  # (re)seed, no alert
    python3 scripts/bot_integrity_watch.py --print     # show, never alert

Cron (every 10 min):
    */10 * * * * /usr/bin/python3 /home/abcdev/projects/Semi_Telematics_bot/scripts/bot_integrity_watch.py >> /home/abcdev/backups/bot_integrity.log 2>&1
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = Path.home() / ".bot_integrity"
STATE_FILE = STATE_DIR / "state.json"
KURS_ENV = Path.home() / "projects" / "Kurs_uzbekistan" / ".env"

# The compromised account to keep eyes on (2026-08-11 incident).
WATCH_USER_ID = os.getenv("BOT_WATCH_USER_ID", "356944357")

# Language codes to sweep — the attacker hid defacement in 'en'.
LANGS = ("", "en", "ru", "uz")

TIMEOUT = 12


# ── env / token discovery ────────────────────────────────────────

def load_env(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def discover_tokens(env: dict) -> list[tuple[str, str]]:
    """[(label, token)] for every bot this server owns.

    Deliberately best-effort per source: a missing kurs checkout or an
    unreachable DB must not stop the rest from being watched.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, tok: str | None) -> None:
        tok = (tok or "").strip()
        if tok and ":" in tok and tok not in seen:
            seen.add(tok)
            found.append((label, tok))

    add("system", env.get("TELEGRAM_SYSTEM_BOT_TOKEN")
        or env.get("TELEGRAM_BOT_TOKEN"))
    add("login", env.get("TELEGRAM_LOGIN_BOT_TOKEN"))

    # Tenant bots: encrypted in accounts.bot_token_encrypted.
    try:
        sys.path.insert(0, str(PROJECT))
        from infra.crypto import decrypt  # noqa: E402
        rows = subprocess.run(
            ["psql", env.get("DATABASE_URL", ""), "-qtA", "-c",
             "SELECT id, COALESCE(bot_token_encrypted,'') FROM accounts "
             "WHERE bot_token_encrypted IS NOT NULL "
             "AND bot_token_encrypted <> ''"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip().splitlines()
        for row in rows:
            if "|" not in row:
                continue
            acct, enc = row.split("|", 1)
            try:
                add(f"tenant:{acct}", decrypt(enc))
            except Exception:
                pass
    except Exception as e:
        print(f"note: tenant-token discovery skipped ({e})")

    # Other projects' bots (same owner, same BotFather exposure).
    kurs = load_env(KURS_ENV)
    for key, val in kurs.items():
        if "TOKEN" in key.upper() and ":" in str(val):
            add("kurs", val)

    for extra in (os.getenv("BOT_WATCH_EXTRA_TOKENS") or "").split(","):
        add("extra", extra)
    return found


# ── telegram reads (never writes) ───────────────────────────────

def api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def fingerprint(token: str) -> dict:
    me = api(token, "getMe")
    if not me.get("ok"):
        # 401 = token revoked (or the bot was transferred away).
        return {"alive": False, "why": me.get("description", "unknown")}
    fp: dict = {"alive": True,
                "username": me["result"].get("username", "?"),
                "langs": {}}
    wh = api(token, "getWebhookInfo")
    fp["webhook"] = (wh.get("result") or {}).get("url") or ""
    mb = api(token, "getChatMenuButton").get("result") or {}
    fp["menu"] = f"{mb.get('type', '?')}|{(mb.get('web_app') or {}).get('url', '')}"
    cmds = api(token, "getMyCommands").get("result") or []
    fp["commands"] = [f"{c.get('command')}:{c.get('description', '')}"
                      for c in cmds]
    for lc in LANGS:
        fp["langs"][lc or "default"] = {
            "name": (api(token, "getMyName", language_code=lc)
                     .get("result") or {}).get("name", ""),
            "desc": (api(token, "getMyDescription", language_code=lc)
                     .get("result") or {}).get("description", ""),
            "short": (api(token, "getMyShortDescription", language_code=lc)
                      .get("result") or {}).get("short_description", ""),
        }
    return fp


def watched_account(token: str) -> dict:
    r = api(token, "getChat", chat_id=WATCH_USER_ID)
    if not r.get("ok"):
        return {"visible": False, "why": r.get("description", "")}
    res = r["result"]
    photos = api(token, "getUserProfilePhotos",
                 user_id=WATCH_USER_ID, limit=1)
    return {
        "visible": True,
        "first_name": res.get("first_name", ""),
        "last_name": res.get("last_name", ""),
        "username": res.get("username", ""),
        "bio": res.get("bio", ""),
        "photo_sets": (photos.get("result") or {}).get("total_count", -1),
    }


# ── diffing ─────────────────────────────────────────────────────

def diff(old: dict, new: dict, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            out += diff(old.get(key), new.get(key),
                        f"{path}.{key}" if path else str(key))
    elif old != new:
        def show(v):
            s = "(empty)" if v in ("", None) else str(v)
            return s[:90] + ("…" if len(s) > 90 else "")
        out.append(f"{path}: {show(old)}  →  {show(new)}")
    return out


def notify(env: dict, text: str) -> None:
    token = (env.get("TELEGRAM_SYSTEM_BOT_TOKEN")
             or env.get("TELEGRAM_BOT_TOKEN") or "")
    ids = [i.strip() for i in (env.get("SYSTEM_OWNER_IDS") or "").split(",")
           if i.strip()]
    if not token or not ids:
        print("WARNING: cannot alert — no system token / owner ids")
        return
    for chat in ids:
        api(token, "sendMessage", chat_id=chat,
            text=text[:4000], disable_web_page_preview="true")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    env = load_env(PROJECT / ".env")
    tokens = discover_tokens(env)
    if not tokens:
        print("ERROR: no bot tokens discovered")
        return 1

    current: dict = {"bots": {}, "account": {}}
    for label, tok in tokens:
        fp = fingerprint(tok)
        key = fp.get("username") or f"?{label}"
        fp["label"] = label
        current["bots"][key] = fp
    current["account"] = watched_account(tokens[0][1])

    if mode == "--print":
        print(json.dumps(current, indent=2, ensure_ascii=False))
        return 0

    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    previous = {}
    if STATE_FILE.exists():
        try:
            previous = json.loads(STATE_FILE.read_text())
        except Exception:
            previous = {}

    def save() -> None:
        STATE_FILE.write_text(json.dumps(current, indent=2,
                                         ensure_ascii=False))
        STATE_FILE.chmod(0o600)

    if mode == "--baseline" or not previous:
        save()
        names = ", ".join(f"@{k}" for k in current["bots"])
        print(f"baseline saved for: {names}")
        return 0

    changes = diff(previous, current)
    if not changes:
        print(f"ok — {len(current['bots'])} bot(s) unchanged")
        return 0

    dead = [f"@{k}" for k, v in current["bots"].items()
            if not v.get("alive") and previous.get("bots", {})
            .get(k, {}).get("alive")]
    header = ("🚨 BOT TOKEN REVOKED / TRANSFERRED: " + ", ".join(dead)
              if dead else "⚠️ BOT / ACCOUNT CHANGE DETECTED")
    body = header + "\n\n" + "\n".join(f"• {c}" for c in changes[:25])
    print(body)
    notify(env, body)
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
