"""Accounts CRUD mixin."""

from __future__ import annotations

import logging
from typing import Optional

from .models import Account

logger = logging.getLogger(__name__)


class AccountsMixin:

    async def create_account(self, name: str, tier: str = "free") -> Account:
        """Create a new subscriber account.

        Raises ``ValueError`` when ``name`` collides case-insensitively
        with an existing account — two tenants named "Acme Inc" would be
        indistinguishable in every operator/support surface, so the
        caller must pick a variant.

        The account insert + permission seed + PTI-template seed run in
        ONE transaction: a failure in any seed rolls the account row
        back, so a half-provisioned tenant (login works, permissions
        missing) can never exist.
        """
        clean = (name or "").strip()
        cur = await self._db.execute(
            "SELECT id FROM accounts WHERE LOWER(name) = LOWER(?)",
            (clean,),
        )
        if await cur.fetchone():
            raise ValueError(
                f"An account named '{clean}' already exists. "
                "Choose a different company name."
            )

        now = self._now()
        slug = self._make_slug(clean)
        from features.inspections.templates import (
            STANDARD_DOT_TRUCK_ITEMS,
            STANDARD_DOT_TRAILER_ITEMS,
        )
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT INTO accounts (name, slug, tier, created_at) VALUES (?, ?, ?, ?)",
                (clean, slug, tier, now),
            )
            account_id = cur.lastrowid
            # Seeds run inside the same transaction — their internal
            # ``commit()`` calls are no-ops on the pool proxy; the
            # surrounding BEGIN/COMMIT owns the lifecycle.
            await self.seed_account_permissions(account_id)
            await self.seed_account_pti_templates(
                account_id, STANDARD_DOT_TRUCK_ITEMS, STANDARD_DOT_TRAILER_ITEMS,
            )
        return Account(id=account_id, name=clean, slug=slug,
                       tier=tier, is_active=True, created_at=now)

    async def get_account(self, account_id: int) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def get_account_by_slug(self, slug: str) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE slug = ?", (slug,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def list_accounts(self, active_only: bool = True) -> list[Account]:
        q = "SELECT * FROM accounts"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY name"
        cur = await self._db.execute(q)
        rows = await cur.fetchall()
        return [self._row_to_account(r) for r in rows]

    async def update_account(self, account_id: int, **kwargs) -> bool:
        """Update account fields. Allowed keys: name, tier, is_active, bot_token_encrypted, bot_username, webhook_secret, payroll_enabled, coaching_enabled, timezone, alert_routing_mode, disabled_modules."""
        allowed = {"name", "tier", "is_active", "bot_token_encrypted", "bot_username", "webhook_secret", "payroll_enabled", "coaching_enabled", "timezone", "alert_routing_mode", "disabled_modules"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        await self._db.execute(
            f"UPDATE accounts SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()
        # Tz change invalidates the account-tz cache plus every cached
        # user resolution that may have inherited the default.
        if "timezone" in updates:
            try:
                from capabilities.localization.tz import invalidate_account
                invalidate_account(account_id)
            except Exception:
                pass
        return True

    async def update_account_tier(self, account_id: int, tier: str) -> bool:
        """Shortcut to update only the tier column on accounts."""
        return await self.update_account(account_id, tier=tier)

    async def set_alert_routing_mode(self, account_id: int, mode: str) -> bool:
        """Switch the account between legacy ``single_group`` and the new
        ``per_persona_groups`` routing.  Validates the mode string so a
        typo from the admin UI can't silently fall back to single_group
        in the resolver — better to reject the write than to route
        alerts to the wrong place.
        """
        if mode not in ("single_group", "per_persona_groups"):
            raise ValueError(
                f"Invalid alert_routing_mode {mode!r} — "
                "expected 'single_group' or 'per_persona_groups'"
            )
        return await self.update_account(account_id, alert_routing_mode=mode)

    async def get_accounts_with_bot_tokens(self) -> list[Account]:
        """Return all active accounts that have a bot token configured."""
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE is_active = 1 "
            "AND bot_token_encrypted IS NOT NULL AND bot_token_encrypted != '' "
            "ORDER BY id"
        )
        rows = await cur.fetchall()
        return [self._row_to_account(r) for r in rows]

    # ── Platform audit trail ────────────────────────────────────────

    async def add_platform_audit(
        self,
        event: str,
        *,
        account_id: int | None = None,
        actor: str = "",
        details: str = "",
    ) -> None:
        """Append a system-tier audit row (account created / suspended / …).

        Best-effort by contract: callers wrap this in try/except (or rely
        on it being after their own commit) — an audit-write failure must
        never abort the business action it describes.
        """
        await self._db.execute(
            """
            INSERT INTO platform_audit_log
                (event, account_id, actor, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event, account_id, actor, details, self._now()),
        )
        await self._db.commit()

    async def list_platform_audit(
        self, *, event: str = "", limit: int = 100,
    ) -> list[dict]:
        """Newest-first platform audit rows, optionally filtered by event."""
        if event:
            cur = await self._db.execute(
                "SELECT * FROM platform_audit_log WHERE event = ? "
                "ORDER BY id DESC LIMIT ?",
                (event, limit),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM platform_audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Account lifecycle: suspend / soft-delete / purge ──────────

    async def get_account_lifecycle(self, account_id: int) -> Optional[dict]:
        """Return lifecycle state for one account or None if not found.

        Reads the six lifecycle columns added by migration 095.  Used by
        the auth path (to reject login) and the system operator console
        (to render the Account state card).
        """
        cur = await self._db.execute(
            """
            SELECT id, name, is_active,
                   suspended_at, suspended_reason, suspended_by,
                   deleted_at, delete_requested_by, purge_at
              FROM accounts
             WHERE id = ?
            """,
            (account_id,),
        )
        r = await cur.fetchone()
        if not r:
            return None
        return {
            "account_id":          r[0],
            "name":                r[1],
            "is_active":           bool(r[2]),
            "suspended_at":        r[3],
            "suspended_reason":    r[4],
            "suspended_by":        r[5],
            "deleted_at":          r[6],
            "delete_requested_by": r[7],
            "purge_at":            r[8],
        }

    async def suspend_account(
        self,
        account_id: int,
        *,
        reason: str,
        operator_tg_id: int,
    ) -> bool:
        """Suspend an account: blocks login, scheduler jobs skip it.

        Reversible via ``unsuspend_account``.  Data is preserved.  No-op
        if already suspended (returns False so the caller can surface
        "already suspended" rather than silently re-stamping).
        """
        now = self._now()
        cur = await self._db.execute(
            """
            UPDATE accounts
               SET is_active        = 0,
                   suspended_at     = ?,
                   suspended_reason = ?,
                   suspended_by     = ?
             WHERE id = ?
               AND suspended_at IS NULL
            """,
            (now, reason, operator_tg_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def unsuspend_account(self, account_id: int) -> bool:
        """Reverse ``suspend_account``: clears suspend fields, re-enables login.

        Refuses to unsuspend an account that's currently in deletion grace
        (``deleted_at`` set) — the owner-driven delete flow must be cancelled
        first via ``cancel_account_deletion``.
        """
        cur = await self._db.execute(
            """
            UPDATE accounts
               SET is_active        = 1,
                   suspended_at     = NULL,
                   suspended_reason = NULL,
                   suspended_by     = NULL
             WHERE id = ?
               AND suspended_at IS NOT NULL
               AND deleted_at   IS NULL
            """,
            (account_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def request_account_deletion(
        self,
        account_id: int,
        *,
        owner_tg_id: int,
        grace_days: int = 90,
    ) -> Optional[str]:
        """Start owner-initiated soft-delete: marks account, schedules purge.

        Sets ``deleted_at = now``, ``purge_at = now + grace_days``, disables
        ``is_active`` so login is blocked immediately.  The account is
        recoverable by the owner via ``cancel_account_deletion`` until
        ``purge_at``.

        Returns the ISO ``purge_at`` timestamp on success, or None if the
        account was already in the deletion grace window (idempotent guard).
        """
        from datetime import datetime, timedelta, timezone

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        purge_dt = now_dt + timedelta(days=grace_days)
        purge_iso = purge_dt.isoformat(timespec="seconds").replace("+00:00", "Z")

        cur = await self._db.execute(
            """
            UPDATE accounts
               SET is_active           = 0,
                   deleted_at          = ?,
                   delete_requested_by = ?,
                   purge_at            = ?
             WHERE id = ?
               AND deleted_at IS NULL
            """,
            (now_iso, owner_tg_id, purge_iso, account_id),
        )
        await self._db.commit()
        return purge_iso if cur.rowcount > 0 else None

    async def cancel_account_deletion(self, account_id: int) -> bool:
        """Reverse ``request_account_deletion`` within the grace window.

        Clears delete fields, restores ``is_active=1``.  Returns False if
        the account was never in the deletion window (idempotent guard).
        """
        cur = await self._db.execute(
            """
            UPDATE accounts
               SET is_active           = 1,
                   deleted_at          = NULL,
                   delete_requested_by = NULL,
                   purge_at            = NULL
             WHERE id = ?
               AND deleted_at IS NOT NULL
            """,
            (account_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_accounts_pending_purge(self, *, before_iso: str) -> list[int]:
        """Return account IDs whose grace period has elapsed.

        Called by the daily purge scheduler.  Returns IDs only; the caller
        loads full account rows + tenant data before the hard-delete.
        """
        cur = await self._db.execute(
            """
            SELECT id
              FROM accounts
             WHERE deleted_at IS NOT NULL
               AND purge_at   <= ?
             ORDER BY purge_at
            """,
            (before_iso,),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]

    # ── Owner self-delete verification code ────────────────────────

    @staticmethod
    def _hash_deletion_code(code: str) -> str:
        """SHA-256 of the user-entered code, hex-encoded.

        Codes are short (6 digits) so a plaintext leak would be trivially
        scannable; we store the hash instead.  No salt: the rows are
        single-use, scoped to one account_id, and expire in 15 minutes.
        """
        import hashlib
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    async def create_deletion_code(
        self, account_id: int, user_id: int, *, ttl_minutes: int = 15,
    ) -> str:
        """Mint a 6-digit code for owner self-delete confirmation.

        Replaces any unused code for this account (UNIQUE constraint on
        account_id) so a fresh "send another code" click invalidates the
        previous one.  Returns the plaintext code so the caller can email
        it; only the hash persists.
        """
        import secrets
        from datetime import datetime, timedelta, timezone

        # 6-digit code, leading zeros allowed.  Range 100000–999999 would
        # bias toward shorter codes; randbelow(1_000_000) + zfill keeps
        # entropy uniform across all 1M values.
        code = str(secrets.randbelow(1_000_000)).zfill(6)
        code_hash = self._hash_deletion_code(code)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=ttl_minutes)
        await self._db.execute(
            """
            INSERT INTO account_deletion_codes
                (account_id, user_id, code_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                user_id    = excluded.user_id,
                code_hash  = excluded.code_hash,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at
            """,
            (
                account_id, user_id, code_hash,
                expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
                now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )
        await self._db.commit()
        return code

    async def consume_deletion_code(
        self, account_id: int, user_id: int, code: str,
    ) -> bool:
        """Verify a deletion code; deletes the row on success.

        Returns True iff the code matches, hasn't expired, and was minted
        for this exact ``(account_id, user_id)`` pair.  Single-use: the row
        is deleted whether the verification succeeds or not (success path
        deletes it for next attempt; expired/wrong code stays so the user
        can retry until expiry).  Returns False for any mismatch reason
        (no enumeration risk because the row is account-scoped).
        """
        from datetime import datetime, timezone

        now_iso = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        cur = await self._db.execute(
            """
            SELECT user_id, code_hash, expires_at
              FROM account_deletion_codes
             WHERE account_id = ?
            """,
            (account_id,),
        )
        row = await cur.fetchone()
        if not row:
            return False
        row = dict(row)
        if int(row["user_id"]) != int(user_id):
            return False
        if (row["expires_at"] or "") < now_iso:
            return False
        if row["code_hash"] != self._hash_deletion_code(code):
            return False
        # Success: burn the code so it can't be replayed.
        await self._db.execute(
            "DELETE FROM account_deletion_codes WHERE account_id = ?",
            (account_id,),
        )
        await self._db.commit()
        return True

    async def purge_account_data(self, account_id: int) -> dict[str, int]:
        """Hard-delete EVERY row belonging to one account, then the account.

        The terminal step of the 90-day deletion grace — irreversible.
        Discovers tenant tables dynamically (every table with an
        ``account_id`` column) so a new feature's table can't be missed.
        ``users`` and ``companies`` are deleted AFTER the dynamic loop
        because other tables hold FKs into them (invites.created_by,
        authorized_chats.added_by, …); the ``accounts`` row goes last.

        Returns {table_name: rows_deleted} for the audit log.
        """
        cur = await self._db.execute(
            """
            SELECT DISTINCT table_name
              FROM information_schema.columns
             WHERE column_name = 'account_id'
            """
        )
        tables = [dict(r)["table_name"] for r in await cur.fetchall()]

        deferred = {"users", "companies", "accounts"}
        deleted: dict[str, int] = {}
        for t in tables:
            if t in deferred:
                continue
            try:
                cur = await self._db.execute(
                    f"DELETE FROM {t} WHERE account_id = ?", (account_id,)
                )
                if cur.rowcount:
                    deleted[t] = cur.rowcount
            except Exception as e:
                logger.warning("purge: DELETE FROM %s failed for account %s: %s",
                               t, account_id, e)
        for t in ("users", "companies"):
            try:
                cur = await self._db.execute(
                    f"DELETE FROM {t} WHERE account_id = ?", (account_id,)
                )
                if cur.rowcount:
                    deleted[t] = cur.rowcount
            except Exception as e:
                logger.warning("purge: DELETE FROM %s failed for account %s: %s",
                               t, account_id, e)
        cur = await self._db.execute(
            "DELETE FROM accounts WHERE id = ?", (account_id,)
        )
        if cur.rowcount:
            deleted["accounts"] = cur.rowcount
        await self._db.commit()
        return deleted

    async def list_accounts_purging_within(self, *, days: int) -> list[dict]:
        """Accounts whose purge lands within ``days`` from now — for the
        warning email the scheduler sends ahead of the hard delete."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        lo = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        hi = (now + timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        cur = await self._db.execute(
            """
            SELECT id, name, purge_at, delete_requested_by
              FROM accounts
             WHERE deleted_at IS NOT NULL
               AND purge_at > ? AND purge_at <= ?
             ORDER BY purge_at
            """,
            (lo, hi),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def cleanup_expired_deletion_codes(self) -> int:
        """Daily housekeeping: drop expired codes.

        Returns the count deleted.  Called by the same scheduler that
        runs the purge job — codes left around past their TTL are dead
        weight but not a security risk (they can't be used).
        """
        from datetime import datetime, timezone

        now_iso = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        cur = await self._db.execute(
            "DELETE FROM account_deletion_codes WHERE expires_at < ?",
            (now_iso,),
        )
        await self._db.commit()
        return cur.rowcount or 0
