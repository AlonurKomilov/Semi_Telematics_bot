#!/usr/bin/env python3
"""Rotate ENCRYPTION_KEY across every encrypted field in the database.

Why this script exists
----------------------
``infra/crypto.py`` derives a Fernet cipher from ``ENCRYPTION_KEY``.  Three
fields are encrypted at rest:

* ``companies.samsara_api_key``                     — per-account Samsara API key
* ``accounts.bot_token_encrypted``                  — per-account Telegram bot token
* ``account_settings.value`` where ``key='storage.gdrive.refresh_token'``
                                                    — per-account Drive OAuth refresh token

Changing ``ENCRYPTION_KEY`` invalidates every existing ciphertext.  Without
this script, rotation means:

  1. Update ``ENCRYPTION_KEY``.
  2. The bot reboots and tries to decrypt the stored values → ``ValueError``.
  3. Production goes down until every secret is re-entered by hand.

This script re-encrypts every row using the new key, transactionally, with
a dry-run preview and idempotent re-run behavior so a half-finished
rotation can be resumed without breaking anything.

Usage
-----
    OLD_ENCRYPTION_KEY='<current value>' \\
    NEW_ENCRYPTION_KEY='<new value>' \\
    DATABASE_URL='postgresql://...'    \\
    python3 scripts/rotate_encryption_key.py {dry-run|apply}

After a successful ``apply`` run, set ``ENCRYPTION_KEY=$NEW_ENCRYPTION_KEY``
in the deployed environment and restart the API + bot + queue services.

Idempotency
-----------
Re-running after a crash mid-rotation is safe.  Each row is examined and
classified:

  * decrypts with OLD only           → rotate (this is the normal case)
  * decrypts with NEW already        → SKIP, "already rotated" — counted
  * decrypts with neither            → ERROR, abort the transaction
  * does not have the ``enc::`` prefix → SKIP, "plaintext legacy value"

The transaction commits only if every row was classifiable; an unknown
ciphertext aborts everything so partial rotations don't strand the
database in a mixed-key state.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from dataclasses import dataclass, field

try:
    import asyncpg
except ImportError:
    sys.stderr.write("ERROR: asyncpg not installed.  Run `pip install asyncpg`.\n")
    sys.exit(2)

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    sys.stderr.write("ERROR: cryptography not installed.  Run `pip install cryptography`.\n")
    sys.exit(2)

# Must match infra/crypto.py — changing either invalidates every ciphertext.
_APP_SALT = b"semi-telematics-bot-v1"
_ENC_PREFIX = "enc::"
_PBKDF2_ITERATIONS = 480_000


def _build_cipher(passphrase: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_APP_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode())))


@dataclass
class Target:
    """One column in one table that holds Fernet ciphertext."""
    table: str
    column: str
    # ``key_cols`` identifies a unique row.  Composite keys (e.g.
    # account_settings) need more than one column.
    key_cols: tuple[str, ...]
    # Optional WHERE that narrows which rows to consider (account_settings
    # has many keys but only one is encrypted).
    where_extra: str = ""

    def select_sql(self) -> str:
        cols = ", ".join(self.key_cols) + f", {self.column}"
        sql = f"SELECT {cols} FROM {self.table} WHERE {self.column} LIKE 'enc::%'"
        if self.where_extra:
            sql += f" AND ({self.where_extra})"
        return sql

    def update_sql(self) -> str:
        # ``$1`` is the new ciphertext; ``$2..`` map to key_cols in order.
        where = " AND ".join(f"{c} = ${i + 2}" for i, c in enumerate(self.key_cols))
        return f"UPDATE {self.table} SET {self.column} = $1 WHERE {where}"


TARGETS: list[Target] = [
    Target("companies", "samsara_api_key", ("id",)),
    Target("accounts", "bot_token_encrypted", ("id",)),
    Target(
        "account_settings", "value", ("account_id", "key"),
        where_extra="key = 'storage.gdrive.refresh_token'",
    ),
]


@dataclass
class Outcome:
    rotated: int = 0
    already_new: int = 0
    plaintext: int = 0
    unknown: list[str] = field(default_factory=list)


async def _process_target(
    conn, target: Target, old_cipher: Fernet, new_cipher: Fernet, apply: bool,
) -> Outcome:
    out = Outcome()
    rows = await conn.fetch(target.select_sql())
    for row in rows:
        stored = row[target.column]
        if not stored.startswith(_ENC_PREFIX):
            out.plaintext += 1
            continue
        token = stored[len(_ENC_PREFIX):].encode()

        plaintext: str | None = None
        try:
            plaintext = old_cipher.decrypt(token).decode()
            classification = "rotate"
        except InvalidToken:
            try:
                new_cipher.decrypt(token).decode()
                classification = "already_new"
            except InvalidToken:
                classification = "unknown"

        key_repr = ", ".join(f"{c}={row[c]!r}" for c in target.key_cols)

        if classification == "already_new":
            out.already_new += 1
            print(f"  SKIP {target.table}({key_repr}): already encrypted with NEW key")
            continue

        if classification == "unknown":
            label = f"{target.table}({key_repr})"
            out.unknown.append(label)
            print(f"  FAIL {label}: ciphertext decrypts with neither OLD nor NEW key")
            continue

        assert plaintext is not None
        new_ciphertext = _ENC_PREFIX + new_cipher.encrypt(plaintext.encode()).decode()

        if apply:
            params: list = [new_ciphertext] + [row[c] for c in target.key_cols]
            await conn.execute(target.update_sql(), *params)
            print(f"  OK   {target.table}({key_repr}): rotated")
        else:
            print(f"  DRY  {target.table}({key_repr}): would rotate")
        out.rotated += 1
    return out


async def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("dry-run", "apply"):
        sys.stderr.write(
            "Usage: rotate_encryption_key.py {dry-run|apply}\n"
            "Env:   OLD_ENCRYPTION_KEY, NEW_ENCRYPTION_KEY, DATABASE_URL\n"
        )
        return 2

    apply = sys.argv[1] == "apply"

    old_key = os.environ.get("OLD_ENCRYPTION_KEY", "").strip()
    new_key = os.environ.get("NEW_ENCRYPTION_KEY", "").strip()
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not old_key:
        sys.stderr.write("ERROR: OLD_ENCRYPTION_KEY is empty.\n")
        return 2
    if not new_key:
        sys.stderr.write("ERROR: NEW_ENCRYPTION_KEY is empty.\n")
        return 2
    if not db_url:
        sys.stderr.write("ERROR: DATABASE_URL is empty.\n")
        return 2
    if old_key == new_key:
        sys.stderr.write("ERROR: OLD and NEW keys are identical — nothing to rotate.\n")
        return 2

    old_cipher = _build_cipher(old_key)
    new_cipher = _build_cipher(new_key)

    print(f"Mode: {'APPLY (writes commit)' if apply else 'DRY-RUN (no writes)'}")
    print(f"Database: {db_url.split('@', 1)[-1] if '@' in db_url else '(local)'}")
    print()

    conn = await asyncpg.connect(db_url)
    totals: dict[str, Outcome] = {}
    try:
        if apply:
            tx = conn.transaction()
            await tx.start()
        for target in TARGETS:
            print(f"── {target.table}.{target.column} ──")
            outcome = await _process_target(conn, target, old_cipher, new_cipher, apply)
            totals[target.table] = outcome
            print(
                f"   rotate={outcome.rotated} "
                f"already_new={outcome.already_new} "
                f"plaintext_skipped={outcome.plaintext} "
                f"unknown={len(outcome.unknown)}"
            )
            print()
        # Any unknown ciphertexts abort the whole rotation — partial
        # success would leave the DB in a mixed-key state that's harder
        # to reason about than a clean retry.
        any_unknown = sum(len(o.unknown) for o in totals.values())
        if any_unknown:
            print(
                f"ABORT: {any_unknown} row(s) could not be decrypted with "
                "either key.  No changes committed.  Investigate before retrying."
            )
            if apply:
                await tx.rollback()
            return 1

        if apply:
            await tx.commit()
            print("COMMITTED.")
        else:
            print("Dry-run complete — re-run with `apply` to commit.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
