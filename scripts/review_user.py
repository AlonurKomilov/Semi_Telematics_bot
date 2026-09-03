"""Create (and later retire) the throwaway account a store reviewer signs in with.

The browser extension's consent step happens on the DASHBOARD, so the
Chrome Web Store reviewer needs a real sign-in — and that sign-in gets
whatever the role permits, on a real customer's data.  So the account
this makes is the narrowest one that can still see a live map:

  * role DRIVER — the one role that carries ``can_view_location`` and
    NOT ONE ``can_manage_*``: it cannot write anything.  (Dispatcher,
    the next-narrowest, can edit loads, geofences and inspections.)
  * two or three real trucks, assigned as NON-primary rows, so the map
    shows exactly those and nothing else.  A driver with no assignment
    is NOT an empty map — deps.filter_by_assigned_trucks keeps legacy
    behaviour and shows every vehicle — so --trucks is REQUIRED.
  * ``users.vehicle_scope = 'assigned'`` pinned on the member, one
    company only, email pre-verified (no inbox), password printed once.
  * the account's EFFECTIVE driver permissions are resolved the way
    request auth resolves them (seed + stored override for the chosen
    company) and printed; --apply refuses if any write, invite, camera
    or account-wide flag is on — an account that widened "driver" is
    not a place to put a stranger.

What the reviewer WILL still see, knowingly: the assigned trucks' live
positions, their documents, and any real driver paired with them on
scorecards and safety events.  Nothing about anyone else.

    python3 -m scripts.review_user --account 10000001 --company PTG --trucks 142,143,220 --email you@yours.com
    python3 -m scripts.review_user ... --apply
    python3 -m scripts.review_user --account 10000001 --email you@yours.com --delete --apply

Dry-run is the default and prints exactly what --apply would do.  Use
an email YOU control: "forgot password" mails a reset link there.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import secrets
import string
import sys
from datetime import datetime, timezone

import asyncpg
from dotenv import load_dotenv

load_dotenv()

ROLE = "driver"
DISPLAY_NAME = "Chrome Web Store review"
#: Letters and digits only: the password is typed by hand from a form
#: field into a browser, and the policy asks for a letter and a digit.
ALPHABET = string.ascii_letters + string.digits
#: Flags that must be OFF for a stranger, whatever the account's matrix says.
WIDE_PREFIXES = ("can_manage_",)
WIDE_SUFFIXES = ("_all",)
WIDE_EXACT = {"can_invite", "can_view_cameras", "can_manage_users"}


def now_iso() -> str:
    """The format every other writer uses (``Database._now``).  Postgres'
    own ``now()::text`` renders a space and a colon-less offset — the
    same instant, a different string, and these columns are sorted as
    TEXT."""
    return datetime.now(timezone.utc).isoformat()


def new_password(length: int = 24) -> str:
    while True:
        pw = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def hash_password(password: str) -> str:
    """The same hash the API's own signup writes."""
    from interfaces.api.auth import _hash_password
    return _hash_password(password)


async def find_user(conn, email: str, account_id: int):
    return await conn.fetchrow(
        "SELECT id, role, account_id, is_active, display_name FROM users "
        "WHERE lower(email) = lower($1) AND account_id = $2",
        email, account_id)


async def effective_driver_perms(conn, account_id: int, company_id: int) -> dict:
    """Seed + the account's stored override, merged the way
    capabilities.permissions.roles._resolve_perms merges them (company
    override first, else account-wide, else seed).  Module masking and
    service derivation are skipped: they only ever REMOVE or add
    read-only service flags, never a write."""
    from adapters.storage import Role
    from capabilities.permissions.roles import ROLE_PERMISSIONS, normalize_stored_perm_keys
    seed = dataclasses.asdict(ROLE_PERMISSIONS[Role.DRIVER])
    row = await conn.fetchrow(
        "SELECT permissions FROM role_permissions WHERE account_id = $1 AND role = $2 AND company_id = $3",
        account_id, ROLE, company_id)
    if not row:
        row = await conn.fetchrow(
            "SELECT permissions FROM role_permissions WHERE account_id = $1 AND role = $2 AND company_id IS NULL",
            account_id, ROLE)
    if not row:
        return seed
    stored = json.loads(row["permissions"]) if isinstance(row["permissions"], str) else dict(row["permissions"])
    stored = normalize_stored_perm_keys(stored)
    return {**seed, **{k: v for k, v in stored.items() if k in seed}}


def wide_flags(perms: dict) -> list[str]:
    return sorted(
        k for k, v in perms.items() if v is True and (
            k in WIDE_EXACT or k.startswith(WIDE_PREFIXES) or k.endswith(WIDE_SUFFIXES)))


async def create(conn, *, email: str, account_id: int, company: str,
                 trucks: list[str], apply: bool) -> int:
    if not trucks:
        print("REFUSING: --trucks is required.  A driver with no assignment sees EVERY vehicle "
              "(legacy behaviour in deps.filter_by_assigned_trucks), not an empty map.")
        return 2
    existing = await find_user(conn, email, account_id)
    if existing:
        print(f"REFUSING: {email} already exists on account {account_id} "
              f"(id={existing['id']}, role={existing['role']}).  "
              f"Retire it first (--delete) or pick another --email.")
        return 1

    acct = await conn.fetchrow("SELECT id, name FROM accounts WHERE id = $1", account_id)
    if not acct:
        print(f"ERROR: no account {account_id}")
        return 2
    co = await conn.fetchrow(
        "SELECT id, code, display_name FROM companies "
        "WHERE account_id = $1 AND upper(code) = upper($2) AND is_active = 1",
        account_id, company)
    if not co:
        have = await conn.fetch(
            "SELECT code FROM companies WHERE account_id = $1 AND is_active = 1 ORDER BY code", account_id)
        print(f"ERROR: no company {company!r} on account {account_id}.  "
              f"Have: {', '.join(r['code'] for r in have)}")
        return 2

    # The trucks must be real, active, this company's, not trailers —
    # and no unit may be a substring of another unit in the company:
    # one live-endpoint match is still by substring (a pre-existing
    # over-match), and a reviewer must not see a truck by accident.
    units = await conn.fetch(
        "SELECT unit_number FROM vehicles WHERE account_id = $1 AND company_code = $2 "
        "AND is_active = 1 AND coalesce(archived_reason, '') = '' "
        "AND coalesce(vehicle_type, 'truck') <> 'trailer'",
        account_id, co["code"])
    known = {r["unit_number"] for r in units}
    missing = [t for t in trucks if t not in known]
    if missing:
        print(f"ERROR: not active trucks of {co['code']}: {', '.join(missing)}")
        return 2
    clashing = [t for t in trucks if any(t != o and t in o for o in known)]
    if clashing:
        print(f"REFUSING: unit(s) {', '.join(clashing)} are substrings of other units in {co['code']} "
              f"— pick trucks with distinct numbers.")
        return 2
    primaries = await conn.fetch(
        "SELECT truck_num, user_id FROM driver_trucks WHERE account_id = $1 AND is_primary = 1 "
        "AND truck_num = ANY($2::text[])", account_id, trucks)

    perms = await effective_driver_perms(conn, account_id, co["id"])
    on = sorted(k for k, v in perms.items() if v is True)
    wide = wide_flags(perms)
    role_scope = await conn.fetchval(
        "SELECT scope FROM role_vehicle_scope WHERE account_id = $1 AND role = $2", account_id, ROLE)

    password = new_password()
    print(f"account    {acct['id']}  {acct['name']}")
    print(f"email      {email}")
    print(f"role       {ROLE}")
    print(f"company    {co['code']} ({co['display_name']}) only")
    print(f"trucks     {', '.join(trucks)}  (non-primary; "
          f"{len(primaries)} of them already have a primary driver — untouched)")
    print(f"scope      users.vehicle_scope='assigned'  (account's driver layer: {role_scope or 'built-in default'})")
    print(f"effective  {len(on)} flags on for driver in {co['code']}: {', '.join(on)}")
    if wide:
        print(f"\nREFUSING: this account's driver role carries write/wide flags: {', '.join(wide)}.  "
              f"Narrow the role in Permissions first, or do not put a stranger on this account.")
        return 3
    print(f"password   {password}")
    if not apply:
        print("\nDRY-RUN — nothing was written.  Re-run with --apply.")
        print("The password above is NOT the one --apply will write: it makes a new one.")
        return 0

    ts = now_iso()
    async with conn.transaction():
        user_id = await conn.fetchval(
            """INSERT INTO users
               (telegram_id, account_id, role, display_name, email, password_hash,
                is_primary_owner, email_verified, vehicle_scope, created_at)
               VALUES (NULL, $1, $2, $3, lower($4), $5, 0, 1, 'assigned', $6)
               RETURNING id""",
            account_id, ROLE, DISPLAY_NAME, email, hash_password(password), ts)
        await conn.execute(
            """INSERT INTO user_companies (user_id, account_id, company_id, assigned_by, assigned_at)
               VALUES ($1, $2, $3, 0, $4)""",
            user_id, account_id, co["id"], ts)
        for t in trucks:
            await conn.execute(
                """INSERT INTO driver_trucks (user_id, account_id, truck_num, is_primary, assigned_by, assigned_at)
                   VALUES ($1, $2, $3, 0, 0, $4)""",
                user_id, account_id, t, ts)
    print(f"\nCREATED user id={user_id}.  Write the password down now — it is not stored anywhere else.")
    print("Before you hand it over: sign in as this user yourself and walk every sidebar item, "
          "the Alerts inbox and the AI assistant.  Whatever you see, the reviewer sees.")
    print("Retire it the day the store item is approved:")
    print(f"  python3 -m scripts.review_user --account {account_id} --email {email} --delete --apply")
    return 0


async def delete(conn, *, email: str, account_id: int, apply: bool) -> int:
    """Retire the reviewer's account the way the product retires a
    member: deactivate, don't DELETE (``users.id`` is an FK target and
    the audit trail).  ``get_user_by_email`` filters ``is_active = 1``
    and refresh checks it, so sign-in and renewal stop.  What the
    product's own remove_user does NOT do is revoke live sessions and
    denylist their tokens — and the denylist is the ONLY thing
    get_current_user consults, so without it a token already in the
    reviewer's browser would keep returning live positions for the
    rest of its 30 days."""
    row = await find_user(conn, email, account_id)
    if not row:
        print(f"Nothing to retire: no {email} on account {account_id}.")
        return 0
    if (row["display_name"] or "") != DISPLAY_NAME:
        # A typo in --account, or this email belonging to a real person,
        # would otherwise deactivate someone mid-shift.
        print(f"REFUSING: user {row['id']} is not one this script created "
              f"(display_name {row['display_name']!r}, expected {DISPLAY_NAME!r}).")
        return 1
    uid = row["id"]
    live = await conn.fetch(
        "SELECT id, jti, expires_at, device_label FROM user_sessions "
        "WHERE user_id = $1 AND revoked_at IS NULL", uid)
    trucks = await conn.fetchval("SELECT count(*) FROM driver_trucks WHERE user_id = $1", uid)
    pushes = await conn.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", uid)
    print(f"user id={uid} role={row['role']} account={account_id} active={bool(row['is_active'])}")
    print(f"live sessions={len(live)} ({', '.join(s['device_label'] or '?' for s in live) or 'none'})  "
          f"truck rows={trucks}  push subscriptions={pushes}")
    if not apply:
        print("\nDRY-RUN — nothing changed.  Re-run with --apply.")
        return 0
    ts = now_iso()
    async with conn.transaction():
        await conn.execute(
            "UPDATE user_sessions SET revoked_at = $2 WHERE user_id = $1 AND revoked_at IS NULL", uid, ts)
        await conn.execute("DELETE FROM driver_trucks WHERE user_id = $1", uid)
        await conn.execute("DELETE FROM user_companies WHERE user_id = $1", uid)
        await conn.execute("DELETE FROM push_subscriptions WHERE user_id = $1", uid)
        await conn.execute(
            "DELETE FROM notification_pref WHERE account_id = $1 AND recipient_type = 'user' AND recipient_id = $2",
            account_id, str(uid))
        await conn.execute("UPDATE users SET is_active = 0 WHERE id = $1", uid)
    # THE STEP THAT ACTUALLY STOPS A LIVE TOKEN.  infra.cache swallows
    # every Redis error by design (the live app must degrade, not lock
    # everyone out), so "no exception" proves nothing here: each write
    # is read back, and only a key that is really there counts.
    from infra.cache import exists as _redis_exists
    from interfaces.api.auth import _denylist_key, mark_jti_revoked
    denied, failed = 0, []
    for s_row in live:
        await mark_jti_revoked(s_row["jti"], s_row["expires_at"])
        if await _redis_exists(_denylist_key(s_row["jti"])):
            denied += 1
        else:
            failed.append(s_row["jti"])
    print(f"RETIRED user {uid}: deactivated; {len(live)} session row(s) revoked, {denied} token(s) "
          f"denylisted and read back; {trucks} truck row(s), company access and {pushes} push "
          f"subscription(s) removed.")
    if failed:
        print(f"WARNING: {len(failed)} token(s) NOT on the denylist after the write: {', '.join(failed)}.  "
              f"Those stay usable until they expire.  Check Redis and re-run --delete --apply "
              f"(it is idempotent: already-revoked rows are skipped, the denylist is rewritten).")
        return 4
    print("Sign-in and refresh are refused from now on, and every token already issued is "
          "refused at its next request.")
    return 0


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", type=int, required=True, help="account id (PTG is 10000001)")
    p.add_argument("--email", required=True, help="an address YOU control — password resets go there")
    p.add_argument("--company", help="company CODE the reviewer may see (required to create)")
    p.add_argument("--trucks", help="comma-separated unit numbers to assign (required to create; 2-3)")
    p.add_argument("--delete", action="store_true", help="retire the user instead of creating it")
    p.add_argument("--apply", action="store_true", help="write (default: dry-run report)")
    args = p.parse_args()

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url.startswith(("postgresql://", "postgres://")):
        sys.stderr.write("ERROR: DATABASE_URL is not a Postgres DSN.  Set it in .env first.\n")
        return 2

    if args.delete and args.apply:
        # Redis is only ever initialised by the API's startup; a bare
        # script has no pool, and infra.cache then no-ops silently.
        # Without this, "--delete --apply" would report a retired user
        # whose tokens all still work.
        from infra.cache import init_redis
        if not await init_redis():
            sys.stderr.write("ERROR: Redis unreachable — the token denylist cannot be written, so "
                             "retiring would leave every issued token alive.  Fix Redis first.\n")
            return 2

    conn = await asyncpg.connect(db_url)
    try:
        if args.delete:
            return await delete(conn, email=args.email, account_id=args.account, apply=args.apply)
        if not args.company:
            print("ERROR: --company is required to create")
            return 2
        trucks = [t.strip() for t in (args.trucks or "").split(",") if t.strip()]
        return await create(conn, email=args.email, account_id=args.account,
                            company=args.company, trucks=trucks, apply=args.apply)
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
