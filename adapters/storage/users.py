"""Users CRUD mixin — includes subscriber queries and system stats."""

from __future__ import annotations

import logging
from typing import Optional

from .models import Role, User

logger = logging.getLogger("bot.storage.users")


def _matrix_reader_enabled() -> bool:
    """Feature flag for the notifications reader flip (phase 2b-2).

    OFF by default → the legacy alert_* columns stay the SSOT.  Flip
    ``NOTIFICATIONS_MATRIX_READER=1`` (needs a restart, so the backfill
    migration has run first) to route the typed-subscriber reader
    through the proven-equivalent matrix.  Read per-call so it's
    monkeypatchable in tests; the cost is negligible (subscriber fetches
    are per-alert, not a hot loop).
    """
    import os
    return os.getenv("NOTIFICATIONS_MATRIX_READER", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


class UsersMixin:

    async def create_user(
        self, telegram_id: int, account_id: int,
        role: Role = Role.FLEET,
        truck_num: Optional[str] = None,
        display_name: str = "",
    ) -> User:
        """Register a Telegram user to an account."""
        now = self._now()
        # A Telegram /register creates the account's FIRST owner — the
        # PRIMARY owner.  Any other role is a normal member.
        is_primary = 1 if role == Role.OWNER else 0
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, truck_num, display_name,
                is_primary_owner, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, account_id, role.value, truck_num, display_name,
             is_primary, now),
        )
        await self._db.commit()
        return User(
            id=cur.lastrowid, telegram_id=telegram_id,
            account_id=account_id, role=role,
            truck_num=truck_num,
            display_name=display_name,
            alerts_on=False, is_active=True, created_at=now,
            is_primary_owner=bool(is_primary),
        )

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Look up a user by their Telegram chat/user ID."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Look up a user by their internal primary key.  Returns
        terminated users too — callers that want to hide them must
        filter on ``termination_date`` themselves (the driver-list
        endpoint does this)."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Look up a user by email address."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (email.lower().strip(),),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_email_in_account(
        self, email: str, account_id: int,
    ) -> Optional[User]:
        """Look up a user by email within a specific account."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE email = ? AND account_id = ? AND is_active = 1",
            (email.lower().strip(), account_id),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def set_user_email_password(
        self, user_id: int, email: str, password_hash: str,
        *, reset_verification: bool = False,
    ) -> None:
        """Set email and password hash for an existing user.

        ``reset_verification=True`` forces ``email_verified`` back to 0
        so the new (or changed) email must be confirmed before it can
        be used to sign in.  Call sites that ADD or CHANGE the email
        (e.g. the Profile "Add email" form) pass True; the password-
        reset path leaves verification status untouched because the
        email itself didn't change.
        """
        if reset_verification:
            await self._db.execute(
                "UPDATE users SET email = ?, password_hash = ?, "
                "email_verified = 0 WHERE id = ?",
                (email.lower().strip(), password_hash, user_id),
            )
        else:
            await self._db.execute(
                "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
                (email.lower().strip(), password_hash, user_id),
            )
        await self._db.commit()

    async def create_pending_user(
        self, account_id: int, role: Role, display_name: str,
        email: str | None = None,
    ) -> int:
        """Provision a member with NO login identity — telegram NULL, no
        password — so Team Management shows them as "pending" until they
        sign in (link Telegram via invite, or set an email password).
        Used by the integration-linking helper ("add as pending user") so
        synced drivers/dispatchers continue THEIR data instead of a fresh
        account being created later.  Returns the new user id."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, display_name, email, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                None, account_id,
                role.value if hasattr(role, "value") else str(role),
                display_name,
                (email or "").lower().strip() or None,
                now,
            ),
        )
        await self._db.commit()
        return int(cur.lastrowid)

    async def create_user_with_email(
        self, email: str, password_hash: str, account_id: int,
        role: Role = Role.FLEET,
        display_name: str = "",
    ) -> User:
        """Create a user that signed up via email + password.

        ``telegram_id`` is left NULL on the row — the user can later
        link a Telegram account through ``link_telegram_to_user`` and
        the column gets filled in then.  Postgres permits multiple
        NULL values in a UNIQUE constraint, so the existing
        ``UNIQUE(telegram_id)`` doesn't need any change.

        Historical note: this method used to synthesise a negative
        SHA-256-derived placeholder so the NOT NULL constraint stayed
        satisfied.  That placeholder leaked across the auth surface in
        confusing ways (JWT ``sub`` was negative, ownership comparisons
        broke on Telegram link, etc.).  Dropping the placeholder means
        every consumer that reads ``telegram_id`` must tolerate None —
        ``User.label`` and ``users.py`` lookup paths already handle it.
        """
        now = self._now()
        # The account's FIRST owner (every signup flow that mints an OWNER
        # here) is the PRIMARY owner — the un-demotable seat.  Co-owners are
        # created later via the promote-owner flow with is_primary_owner=0.
        is_primary = 1 if role == Role.OWNER else 0
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, display_name,
                email, password_hash, is_primary_owner, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (None, account_id, role.value,
             display_name, email.lower().strip(), password_hash, is_primary, now),
        )
        await self._db.commit()
        return User(
            id=cur.lastrowid, telegram_id=None,
            account_id=account_id, role=role,
            truck_num=None,
            display_name=display_name, email=email.lower().strip(),
            password_hash=password_hash,
            alerts_on=False, is_active=True, created_at=now,
            is_primary_owner=bool(is_primary),
        )

    async def link_telegram_to_user(self, user_id: int, telegram_id: int) -> None:
        """Link a real Telegram ID to an email-registered user."""
        await self._db.execute(
            "UPDATE users SET telegram_id = ? WHERE id = ?",
            (telegram_id, user_id),
        )
        await self._db.commit()

    async def unlink_telegram_from_user(self, user_id: int) -> None:
        """Detach the Telegram link from a user, leaving them with email
        sign-in only.  Refuses to unlink the last sign-in method — a
        Telegram-only user without an email + password would otherwise
        end up with no way to sign in at all."""
        cur = await self._db.execute(
            "SELECT email, password_hash FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return
        email = dict(row).get("email")
        pw = dict(row).get("password_hash")
        if not email or not pw:
            raise ValueError(
                "Cannot unlink Telegram: no email + password is set on this "
                "account, so removing the Telegram link would leave the user "
                "with no way to sign in.",
            )
        await self._db.execute(
            "UPDATE users SET telegram_id = NULL WHERE id = ?", (user_id,),
        )
        await self._db.commit()

    async def get_user(self, user_id: int) -> Optional[User]:
        cur = await self._db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def list_account_users(self, account_id: int) -> list[User]:
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND is_active = 1 ORDER BY role, created_at",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def count_account_users(self, account_id: int) -> int:
        """Return the number of active users for an account."""
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM users WHERE account_id = ? AND is_active = 1",
            (account_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def list_all_users_cross_account(
        self,
        search: str = "",
        limit: int = 200,
    ) -> list[dict]:
        """Operator-side cross-account user search (system console).

        Joined with ``accounts.name`` so the operator sees "Jane —
        ACME TRUCKING (owner)" without a per-row lookup.  ``search``
        matches display_name (case-insensitive substring), email, or an
        exact telegram_id.  Newest first.  This is the only place we
        intentionally read users across tenant boundaries — gated by
        ``require_system_owner`` at the route.
        """
        where = ["u.account_id = a.id"]
        params: list = []
        needle = (search or "").strip()
        if needle:
            clause = "(LOWER(u.display_name) LIKE ? OR LOWER(COALESCE(u.email,'')) LIKE ?"
            params.append(f"%{needle.lower()}%")
            params.append(f"%{needle.lower()}%")
            if needle.isdigit():
                clause += " OR u.telegram_id = ?"
                params.append(int(needle))
            clause += ")"
            where.append(clause)
        params.append(limit)
        cur = await self._db.execute(
            f"""
            SELECT u.id, u.telegram_id, u.account_id, a.name AS account_name,
                   u.role, u.display_name, u.email, u.is_active,
                   u.created_at, u.last_seen
            FROM users u, accounts a
            WHERE {" AND ".join(where)}
            ORDER BY u.created_at DESC, u.id DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def create_user_session(
        self,
        *,
        user_id: int,
        jti: str,
        device_label: str,
        user_agent: str,
        ip: str,
        created_at: str,
        last_seen: str,
        expires_at: str,
    ) -> int | None:
        """Record a freshly-minted JWT as an active session.

        Called from each login flow in ``interfaces/api/auth.py`` after
        ``create_jwt``.  Returns the new row id, or ``None`` if the row
        couldn't be inserted (e.g. jti collision — astronomically
        unlikely with ``secrets.token_urlsafe(16)``).
        """
        try:
            cur = await self._db.execute(
                """INSERT INTO user_sessions
                   (user_id, jti, device_label, user_agent, ip,
                    created_at, last_seen, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, jti, device_label, user_agent, ip,
                 created_at, last_seen, expires_at),
            )
            await self._db.commit()
            return cur.lastrowid
        except Exception:
            return None

    async def update_user_session_on_refresh(
        self, jti: str, new_expires_at: str, now_iso: str,
    ) -> None:
        """Extend an existing session row's expiry on JWT refresh.

        Refresh re-issues a token with a fresh ``exp`` but the **same**
        jti — the underlying browser session is unchanged, so we update
        in place rather than insert a duplicate row.  Silent no-op when
        the jti isn't found (e.g. session was revoked or never recorded).
        """
        await self._db.execute(
            "UPDATE user_sessions SET expires_at = ?, last_seen = ? WHERE jti = ?",
            (new_expires_at, now_iso, jti),
        )
        await self._db.commit()

    async def touch_user_session_last_seen(self, jti: str, now_iso: str) -> None:
        """Stamp the heartbeat on one session row.  Silent no-op if the
        jti isn't found (legacy tokens minted before user_sessions
        existed, or session row was deleted)."""
        await self._db.execute(
            "UPDATE user_sessions SET last_seen = ? WHERE jti = ?",
            (now_iso, jti),
        )
        await self._db.commit()

    async def revoke_user_session(
        self, session_id: int, *, owning_user_id: int | None = None,
    ) -> dict | None:
        """Mark a single session as revoked.

        When ``owning_user_id`` is provided, the session is only revoked
        if it belongs to that user (the customer "revoke my device"
        path).  When omitted, no ownership check is applied — operator-
        only path, gated at the route by ``require_system_owner``.

        Returns the revoked row's jti + expires_at so the caller can
        push the jti onto the Redis denylist with the correct TTL.
        ``None`` when the row didn't exist, was already revoked, or
        failed the ownership check.
        """
        # SELECT-then-UPDATE so we can return jti + expires_at without
        # asking Postgres for RETURNING (keeps the SQLite-style adapter
        # path working).  Race-free: a duplicate revoke from a second
        # tab still ends up with the same revoked row, no harm done.
        where = ["id = ?", "revoked_at IS NULL"]
        params: list = [session_id]
        if owning_user_id is not None:
            where.append("user_id = ?")
            params.append(owning_user_id)
        cur = await self._db.execute(
            f"""SELECT id, jti, expires_at, user_id
                FROM user_sessions
                WHERE {" AND ".join(where)}
                LIMIT 1""",
            params,
        )
        row = await cur.fetchone()
        if not row:
            return None
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE id = ?",
            (now_iso, row["id"]),
        )
        await self._db.commit()
        return {
            "id": row["id"],
            "jti": row["jti"],
            "expires_at": row["expires_at"],
            "user_id": row["user_id"],
        }

    async def revoke_user_session_by_jti(self, jti: str) -> dict | None:
        """Mark a session as revoked by its jti.

        Used by the ``/auth/logout`` endpoint — that path doesn't know
        the row id, only the jti carried in the JWT.  Returns the row's
        ``{jti, expires_at, user_id}`` so the caller can push the jti
        onto the Redis denylist with the right TTL.  ``None`` when no
        row exists or it was already revoked.
        """
        cur = await self._db.execute(
            """SELECT id, jti, expires_at, user_id FROM user_sessions
               WHERE jti = ? AND revoked_at IS NULL LIMIT 1""",
            (jti,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE id = ?",
            (now_iso, row["id"]),
        )
        await self._db.commit()
        return {
            "id": row["id"],
            "jti": row["jti"],
            "expires_at": row["expires_at"],
            "user_id": row["user_id"],
        }

    async def revoke_account_sessions(self, account_id: int) -> list[dict]:
        """Revoke every active session for every user in one account.

        Used by account suspend and owner-initiated deletion: both must
        kill existing logins immediately, not just block new ones.
        Returns the revoked rows ({jti, expires_at}) so the caller can
        fan them out to the Redis denylist.
        """
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """SELECT s.id, s.jti, s.expires_at
                 FROM user_sessions s
                 JOIN users u ON u.id = s.user_id
                WHERE u.account_id = ? AND s.revoked_at IS NULL""",
            (account_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return []
        await self._db.execute(
            """UPDATE user_sessions SET revoked_at = ?
                WHERE revoked_at IS NULL
                  AND user_id IN (SELECT id FROM users WHERE account_id = ?)""",
            (now_iso, account_id),
        )
        await self._db.commit()
        return rows

    async def revoke_other_user_sessions(
        self, user_id: int, current_jti: str,
    ) -> list[dict]:
        """Revoke every active session for ``user_id`` except ``current_jti``.

        Returns the list of revoked rows ({jti, expires_at}) so the
        caller can fan them out to the Redis denylist.  When the user
        has no other sessions, returns an empty list.
        """
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """SELECT id, jti, expires_at FROM user_sessions
               WHERE user_id = ? AND revoked_at IS NULL AND jti <> ?""",
            (user_id, current_jti),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return []
        await self._db.execute(
            """UPDATE user_sessions SET revoked_at = ?
               WHERE user_id = ? AND revoked_at IS NULL AND jti <> ?""",
            (now_iso, user_id, current_jti),
        )
        await self._db.commit()
        return rows

    async def list_user_sessions(
        self,
        user_id: int,
        *,
        include_revoked: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """Return one user's sessions, newest-active first.

        Pass 1 surfaces only the active ones (``revoked_at IS NULL`` and
        not yet past ``expires_at``).  Pass 2 will use
        ``include_revoked=True`` for a revoked-history view.
        """
        where = ["user_id = ?"]
        params: list = [user_id]
        if not include_revoked:
            where.append("revoked_at IS NULL")
            # Expires are stored as ISO UTC; SQLite compares strings
            # lexicographically which is correct for that fixed format.
            where.append("expires_at > ?")
            from datetime import datetime, timezone
            params.append(datetime.now(timezone.utc).isoformat())
        params.append(limit)
        cur = await self._db.execute(
            f"""SELECT id, user_id, jti, device_label, user_agent, ip,
                       created_at, last_seen, expires_at, revoked_at
                FROM user_sessions
                WHERE {" AND ".join(where)}
                ORDER BY last_seen DESC, id DESC
                LIMIT ?""",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def touch_user_last_seen(self, telegram_id: int, now_iso: str) -> None:
        """Stamp ``users.last_seen`` for one user.

        Called (throttled) by ``get_current_user`` on every authenticated
        API request so the operator console can show 'last seen X ago'.
        Throttling lives in the auth dependency — this method just writes.
        Silent no-op if telegram_id doesn't match a row.
        """
        await self._db.execute(
            "UPDATE users SET last_seen = ? WHERE telegram_id = ?",
            (now_iso, telegram_id),
        )
        await self._db.commit()

    async def get_account_admins(self, account_id: int) -> list[User]:
        """Return owners and admins for an account (for error notifications)."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND is_active = 1 "
            "AND role IN ('owner', 'admin') ORDER BY role",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def update_user(self, user_id: int, **kwargs) -> bool:
        """Update user fields. Allowed: role, truck_num, alerts_on, is_active, alert_*."""
        allowed = {"role", "truck_num", "alerts_on", "is_active",
                   "alert_faults", "alert_health", "alert_fuel", "alert_geofence",
                   "alert_events", "alert_parking",
                   "alert_camera",
                   # Per-user "🟢 RESOLVED" DM-receipt toggle (migration 080).
                   # Defaults to 0 on new users; the dashboard "My
                   # Notifications" page is the canonical edit surface.
                   "alert_resolve_receipts",
                   "ai_fault", "ai_health", "ai_fuel", "ai_events", "ai_parking",
                   "quiet_start", "quiet_end", "timezone", "display_name",
                   "language", "samsara_driver_id",
                   # Personal DND toggle (migration 100).  ``True`` =
                   # respect Working Hours, ``False`` = receive alerts
                   # 24/7.  Coerced to int below for SQLite (no native bool).
                   "dnd_enabled",
                   # FK to work_hours.id (migration 101) — admin-set
                   # named-schedule pointer.  Validated at the endpoint
                   # layer (must belong to caller's account).
                   "assigned_work_hours_id",
                   # Manager tier (migration 136) — per-user seniority on the
                   # base role.  Coerced to int below for SQLite.
                   "is_manager",
                   # Primary-owner flag (migration 137).  Coerced to int below.
                   "is_primary_owner"}
        updates = {}
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k == "role" and isinstance(v, Role):
                v = v.value
            # SQLite has no native boolean — store these as INTEGER 0/1.
            # Coerce here so callers can pass real bools.
            if k in ("dnd_enabled", "is_manager", "is_primary_owner") and isinstance(v, bool):
                v = 1 if v else 0
            updates[k] = v
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        await self._db.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()
        # Drop the resolved-tz cache when the column changed so the
        # next display / cron tick picks up the new value within the
        # cache TTL.  Cheap; the helper module no-ops if it's never
        # been imported (it lazy-loads on first call).
        if "timezone" in updates:
            try:
                from capabilities.localization.tz import invalidate_user
                invalidate_user(user_id)
            except Exception:
                pass
        # Write-also: mirror alert-pref changes into the notification
        # matrix so it stays live-fresh during the columns→matrix
        # transition (notifications phase 2b).  Best-effort — a mirror
        # failure must NEVER break the primary column write; the columns
        # remain the reader's SSOT until the reader flip (2b-2).
        try:
            await self._mirror_alert_prefs_to_matrix(user_id, updates)
        except Exception:
            logger.debug("notification-matrix mirror skipped for user %s", user_id)
        return True

    # Map of legacy per-type columns → the matrix's alert_type slug.
    _ALERT_PREF_COLS = {
        "alert_faults": "faults", "alert_health": "health",
        "alert_fuel": "fuel", "alert_geofence": "geofence",
        "alert_events": "events", "alert_parking": "parking",
        "alert_camera": "camera",
    }

    async def _mirror_alert_prefs_to_matrix(self, user_id: int, updates: dict) -> None:
        """Reflect a user's changed alert toggles / master switch into
        ``notification_pref`` + ``notification_channel`` (telegram_dm)."""
        touched = {c: updates[c] for c in self._ALERT_PREF_COLS if c in updates}
        master_changed = "alerts_on" in updates
        if not touched and not master_changed:
            return
        cur = await self._db.execute(
            "SELECT account_id, telegram_id, alerts_on FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return
        account_id, telegram_id, alerts_on = row[0], row[1], row[2]
        for col, val in touched.items():
            # The matrix stores categories namespaced by source; a legacy
            # alert toggle mirrors to the `alert.<type>` category.
            await self.set_notification_pref(
                account_id, "user", user_id, "telegram_dm",
                f"alert.{self._ALERT_PREF_COLS[col]}", enabled=bool(val),
            )
        # The channel connection carries the telegram address + master
        # switch; only meaningful for telegram-linked users.
        if telegram_id is not None:
            await self.upsert_notification_channel(
                account_id, "user", user_id, "telegram_dm",
                address=str(telegram_id), verified=True,
                enabled_master=bool(alerts_on),
            )

    async def toggle_alerts(self, telegram_id: int) -> bool:
        """Toggle alerts_on for a user. Returns new state."""
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return False
        new_val = not user.alerts_on
        await self.update_user(user.id, alerts_on=new_val)
        return new_val

    async def count_users_by_role(self, account_id: int) -> dict[str, int]:
        """``{role: how many ACTIVE people hold it}`` for one account.

        The Permissions page shows this on each role tab, so an owner can
        see the blast radius before toggling: the same switch flipped on a
        role with seven people and on a role with none look identical
        otherwise.  Counts only, never names — the page has no business
        knowing WHO, and a count carries no personal data.

        Active only: a deactivated account holds no permissions today, and
        counting it would overstate what a change reaches.  Roles with
        nobody in them are absent from the map, not zero — the caller
        renders the zero, which keeps "no row" and "0 people" the same
        thing here.
        """
        cur = await self._db.execute(
            "SELECT role, COUNT(*) FROM users "
            " WHERE account_id = ? AND is_active = 1 GROUP BY role",
            (account_id,),
        )
        return {str(r[0]): int(r[1]) for r in await cur.fetchall() if r[0]}

    async def get_alert_subscribers(self, account_id: int) -> list[User]:
        """Users with alerts enabled for an account."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND alerts_on = 1 AND is_active = 1",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_all_alert_subscribers(self) -> list[User]:
        """All users with alerts enabled (across all accounts)."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE alerts_on = 1 AND is_active = 1",
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_typed_alert_subscribers(
        self, account_id: int, alert_type: str,
    ) -> list[User]:
        """Users subscribed to a specific alert type for an account.

        alert_type: 'faults', 'health', 'fuel', 'geofence', 'events',
        'parking', 'camera', or 'maintenance'.

        Two-tier gate:
          1. Personal toggle: ``users.alert_{type} = 1`` AND
             ``alerts_on = 1`` AND ``is_active = 1``.
          2. Permission gate: the user's EFFECTIVE per-account
             permissions must allow this alert type (matrix overrides,
             manager tier, owner rows — same SSOT as the Board's
             visibility filter; §9d closed 2026-07-27).  Without it a
             stale ``alert_fuel = 1`` toggle — or an owner-revoked
             feature — would keep delivering alerts the user may not
             see.

        Reader flip (phase 2b-2): when ``NOTIFICATIONS_MATRIX_READER`` is
        on, delegate to the matrix-backed twin (proven byte-equivalent by
        the shadow-compare test).  Off by default — columns stay the SSOT.
        """
        if _matrix_reader_enabled():
            return await self.get_typed_alert_subscribers_via_matrix(
                account_id, alert_type,
            )
        col = f"alert_{alert_type}"
        if col not in (
            "alert_faults", "alert_health", "alert_fuel", "alert_geofence",
            "alert_events", "alert_parking", "alert_camera",
        ):
            # ``maintenance`` doesn't have a dedicated personal toggle
            # column yet — it currently broadcasts to every user with
            # the maintenance permission.  Future extension: add
            # ``alert_maintenance`` column + a row in the same
            # allow-list.
            return []
        cur = await self._db.execute(
            f"SELECT * FROM users WHERE account_id = ? AND alerts_on = 1"
            f" AND {col} = 1 AND is_active = 1",
            (account_id,),
        )
        rows = await cur.fetchall()
        users = [self._row_to_user(r) for r in rows]

        # EFFECTIVE permission filter (§9d closed 2026-07-27) — the
        # per-account matrix, manager tier, and owner rows all decide
        # delivery, exactly like the Board's visibility filter.  Local
        # import keeps the heavyweight ``capabilities`` package out of
        # this storage module's import graph on cold start.
        from capabilities.alerting.relevance import filter_users_by_alert_access
        return await filter_users_by_alert_access(users, alert_type)

    async def get_typed_alert_subscribers_via_matrix(
        self, account_id: int, alert_type: str,
    ) -> list[User]:
        """Matrix-backed twin of :meth:`get_typed_alert_subscribers`
        (notifications phase 2b).  Reads ``notification_pref`` +
        ``notification_channel`` (telegram_dm) instead of the legacy
        ``alert_*`` columns, then applies the SAME effective-permission
        filter so behavior matches.  NOT yet wired into the live pipeline — the
        shadow-compare test proves it returns the same users before the
        reader flip (2b-2)."""
        # The matrix keys categories namespaced by source.
        subs = await self.get_notification_subscribers(
            account_id, f"alert.{alert_type}", "telegram_dm",
        )
        ids = [int(s["recipient_id"]) for s in subs
               if str(s["recipient_id"]).lstrip("-").isdigit()]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"SELECT * FROM users WHERE account_id = ? AND is_active = 1"
            f" AND id IN ({placeholders})",
            (account_id, *ids),
        )
        users = [self._row_to_user(r) for r in await cur.fetchall()]
        from capabilities.alerting.relevance import filter_users_by_alert_access
        return await filter_users_by_alert_access(users, alert_type)

    async def get_all_typed_subscribers(self, alert_type: str) -> list[User]:
        """All users subscribed to a specific alert type (across all accounts)."""
        col = f"alert_{alert_type}"
        if col not in ("alert_faults", "alert_health", "alert_fuel", "alert_geofence", "alert_events", "alert_parking", "alert_camera"):
            return []
        cur = await self._db.execute(
            f"SELECT * FROM users WHERE alerts_on = 1 AND {col} = 1 AND is_active = 1",
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def count_all_users(self, active_only: bool = True) -> int:
        """Count all users across all accounts."""
        q = "SELECT COUNT(*) FROM users"
        if active_only:
            q += " WHERE is_active = 1"
        cur = await self._db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    async def count_all_companies(self, active_only: bool = True) -> int:
        """Count all companies across all accounts."""
        q = "SELECT COUNT(*) FROM companies"
        if active_only:
            q += " WHERE is_active = 1"
        cur = await self._db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    async def get_system_stats(self) -> dict:
        """Return bot-wide stats for the system owner dashboard."""
        accounts = await self.list_accounts(active_only=False)
        active_accounts = [a for a in accounts if a.is_active]
        inactive_accounts = [a for a in accounts if not a.is_active]

        total_users = await self.count_all_users(active_only=False)
        active_users = await self.count_all_users(active_only=True)
        total_companies = await self.count_all_companies(active_only=False)
        active_companies = await self.count_all_companies(active_only=True)
        alert_subs = await self.get_all_alert_subscribers()

        # Per-account breakdown.  Two bulk queries replace the previous
        # N+1 (one users + one companies query per account).  We group
        # in Python by account_id; row count stays the same but the
        # round-trip cost collapses to 2 regardless of fleet size.
        account_details = []
        active_ids = [a.id for a in active_accounts]
        if active_ids:
            placeholders = ",".join("?" * len(active_ids))
            cur_u = await self._db.execute(
                f"SELECT * FROM users "
                f"WHERE account_id IN ({placeholders}) AND is_active = 1 "
                f"ORDER BY account_id, role, created_at",
                tuple(active_ids),
            )
            users_by_acct: dict[int, list] = {aid: [] for aid in active_ids}
            for r in await cur_u.fetchall():
                u = self._row_to_user(r)
                users_by_acct.setdefault(u.account_id, []).append(u)

            cur_c = await self._db.execute(
                f"SELECT * FROM companies "
                f"WHERE account_id IN ({placeholders}) AND is_active = 1 "
                f"ORDER BY account_id, code",
                tuple(active_ids),
            )
            companies_by_acct: dict[int, list] = {aid: [] for aid in active_ids}
            for r in await cur_c.fetchall():
                co = self._row_to_company(r)
                companies_by_acct.setdefault(co.account_id, []).append(co)
        else:
            users_by_acct, companies_by_acct = {}, {}

        for acct in active_accounts:
            account_details.append({
                "account":   acct,
                "users":     users_by_acct.get(acct.id, []),
                "companies": companies_by_acct.get(acct.id, []),
            })

        return {
            "total_accounts": len(accounts),
            "active_accounts": len(active_accounts),
            "inactive_accounts": len(inactive_accounts),
            "total_users": total_users,
            "active_users": active_users,
            "total_companies": total_companies,
            "active_companies": active_companies,
            "alert_subscribers": len(alert_subs),
            "account_details": account_details,
        }

    async def get_system_extended_stats(self) -> dict:
        """Return extended stats for sysowner: AI usage, alerts, DB size, roles."""
        stats: dict = {}

        # Users per role
        cur = await self._db.execute(
            "SELECT role, COUNT(*) FROM users WHERE is_active=1 GROUP BY role"
        )
        stats["roles"] = {r[0]: r[1] for r in await cur.fetchall()}

        # AI usage per account (last 30 days).  ``created_at`` is TEXT
        # in our schema (ISO-8601 strings); Postgres can't directly
        # compare TEXT > timestamptz, so we cast the column.  The
        # pg_adapter translates ``datetime('now', '-30 days')`` to
        # ``NOW() - INTERVAL '30 days'`` (timestamptz on the right).
        cur = await self._db.execute(
            "SELECT a.name, u.model, COUNT(*) as calls, "
            "COALESCE(SUM(u.total_tokens),0) as tokens "
            "FROM ai_usage u JOIN accounts a ON a.id = u.account_id "
            "WHERE u.created_at::timestamptz > datetime('now', '-30 days') "
            "GROUP BY a.name, u.model ORDER BY tokens DESC"
        )
        stats["ai_usage"] = [
            {"account": r[0], "model": r[1], "calls": r[2], "tokens": r[3]}
            for r in await cur.fetchall()
        ]

        # AI totals
        cur = await self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens),0) FROM ai_usage"
        )
        row = await cur.fetchone()
        stats["ai_total_calls"] = row[0]
        stats["ai_total_tokens"] = row[1]

        # Alert stats (last 7 days) — same TEXT vs timestamptz fix as above.
        cur = await self._db.execute(
            "SELECT alert_type, COUNT(*) as total, "
            "SUM(CASE WHEN acknowledged_at IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) "
            "FROM alert_acknowledgments "
            "WHERE created_at::timestamptz > datetime('now', '-7 days') "
            "GROUP BY alert_type ORDER BY total DESC"
        )
        stats["alerts_7d"] = [
            {"type": r[0], "total": r[1], "acked": r[2], "expired": r[3]}
            for r in await cur.fetchall()
        ]

        # Total alerts all time
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM alert_acknowledgments"
        )
        stats["alerts_total"] = (await cur.fetchone())[0]

        # Active (unacked) alerts right now
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM alert_acknowledgments "
            "WHERE acknowledged_at IS NULL AND status = 'active'"
        )
        stats["alerts_active"] = (await cur.fetchone())[0]

        # Maintenance tasks
        cur = await self._db.execute(
            "SELECT status, COUNT(*) FROM maintenance_tasks GROUP BY status"
        )
        stats["maintenance"] = {r[0]: r[1] for r in await cur.fetchall()}

        # Auto-report subscriptions
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM digest_subscriptions"
        )
        stats["digest_subs"] = (await cur.fetchone())[0]

        # Audit log count
        cur = await self._db.execute("SELECT COUNT(*) FROM audit_log")
        stats["audit_entries"] = (await cur.fetchone())[0]

        # Database size — ``pg_database_size`` is the Postgres equivalent
        # of the old SQLite ``stat(data/bot.db) + -wal + -shm``.  Returns
        # bytes for the current database; converted to MB to match the
        # field's historical units.
        try:
            cur = await self._db.execute(
                "SELECT pg_database_size(current_database())"
            )
            db_bytes = (await cur.fetchone())[0] or 0
            stats["db_size_mb"] = round(db_bytes / 1024 / 1024, 1)
        except Exception:
            # Permission error / unusual deployment — skip gracefully.
            stats["db_size_mb"] = 0.0

        return stats

    async def remove_user(self, user_id: int) -> bool:
        """Soft-delete a user; hard-delete their AI chat history.

        The user row stays (audit trail, FK targets, possible
        re-invite) but their assistant conversations are personal
        free-form content with no operational value once they're off
        the team — privacy-first, delete now rather than waiting for
        the 90-day age-cap.  ``ai_chat_history.user_id`` carries the
        JWT ``sub`` — the telegram_id when the user has one, else the
        ``users.id`` PK (see ``create_jwt``) — so purge both keys,
        scoped to the account.
        """
        cur = await self._db.execute(
            "SELECT account_id, telegram_id FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is not None:
            for chat_uid in {row[1] or 0, user_id} - {0}:
                for table in ("ai_chat_history", "ai_conversations"):
                    await self._db.execute(
                        f"DELETE FROM {table} WHERE account_id = ? AND user_id = ?",
                        (row[0], chat_uid),
                    )
        await self._db.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (user_id,)
        )
        await self._db.commit()
        return True

    # ── Email verification ──────────────────────────────────────────

    async def create_email_verification_token(
        self, user_id: int, email: str, ttl_hours: int = 24,
    ) -> str:
        """Mint a one-time verification token bound to ``user_id``.

        Returns the URL-safe token string.  The caller emails the
        full URL (``<dashboard>/verify-email/<token>``) — the token
        itself never appears in logs or query params we save.
        Expires in ``ttl_hours`` (24h by default — long enough that a
        user can wait until they next check their inbox).
        """
        import secrets
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)
        token = secrets.token_urlsafe(32)
        await self._db.execute(
            """INSERT INTO email_verification_tokens
               (token, user_id, email, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token, user_id, email.lower().strip(),
             now.isoformat(), expires.isoformat()),
        )
        await self._db.commit()
        return token

    async def consume_email_verification_token(
        self, token: str,
    ) -> Optional[int]:
        """Verify and mark a token used.  Returns user_id on success.

        Returns None when the token is unknown, expired, or already
        used.  On success: flip ``users.email_verified=1`` and stamp
        ``used_at`` so the same token can't be redeemed twice.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            "SELECT user_id, expires_at, used_at "
            "FROM email_verification_tokens WHERE token = ?",
            (token,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("used_at"):
            return None
        if (row.get("expires_at") or "") < now:
            return None
        user_id = int(row["user_id"])
        await self._db.execute(
            "UPDATE email_verification_tokens SET used_at = ? "
            "WHERE token = ?", (now, token),
        )
        await self._db.execute(
            "UPDATE users SET email_verified = 1 WHERE id = ?",
            (user_id,),
        )
        await self._db.commit()
        return user_id

    async def is_email_verified(self, user_id: int) -> bool:
        """True when the user has redeemed their verification link.

        Users created before email verification existed are backfilled
        to 1 by the migration so the gate doesn't lock them out.
        """
        cur = await self._db.execute(
            "SELECT email_verified FROM users WHERE id = ?", (user_id,),
        )
        row = await cur.fetchone()
        return bool(row and dict(row).get("email_verified"))

    # ── Failed-login lockout ────────────────────────────────────────

    async def record_failed_login(
        self, user_id: int, max_attempts: int = 5,
        lock_minutes: int = 15,
    ) -> dict:
        """Increment failed-login counter.  Locks when threshold hit.

        Returns ``{attempts, locked_until}`` so the caller can decide
        what to surface in the response (you locked the account / N
        attempts left).  The lock window slides — every NEW failure
        after the threshold extends ``locked_until``, which means a
        determined attacker can't poll while the lock is active.
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        cur = await self._db.execute(
            "SELECT failed_login_attempts FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        prev = int(dict(row).get("failed_login_attempts", 0)) if row else 0
        attempts = prev + 1
        locked_until: Optional[str] = None
        if attempts >= max_attempts:
            locked_until = (now + timedelta(minutes=lock_minutes)).isoformat()
            await self._db.execute(
                "UPDATE users SET failed_login_attempts = ?, "
                "locked_until = ? WHERE id = ?",
                (attempts, locked_until, user_id),
            )
        else:
            await self._db.execute(
                "UPDATE users SET failed_login_attempts = ? WHERE id = ?",
                (attempts, user_id),
            )
        await self._db.commit()
        return {"attempts": attempts, "locked_until": locked_until}

    async def clear_failed_logins(self, user_id: int) -> None:
        """Reset counter + lock on a successful login.

        Called by the login endpoint right after password verification
        succeeds.  Keeps the door open if the user's next attempt is
        legitimately their own typo.
        """
        await self._db.execute(
            "UPDATE users SET failed_login_attempts = 0, "
            "locked_until = NULL WHERE id = ?", (user_id,),
        )
        await self._db.commit()

    async def is_user_locked(self, user_id: int) -> Optional[str]:
        """Return ``locked_until`` ISO timestamp if currently locked,
        ``None`` otherwise.

        Auto-clears the lock when the timestamp has passed, so the
        caller doesn't have to remember to.  This makes the login
        endpoint a single SELECT to know "can this user even try".
        """
        from datetime import datetime, timezone
        cur = await self._db.execute(
            "SELECT locked_until FROM users WHERE id = ?", (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        locked_until = dict(row).get("locked_until")
        if not locked_until:
            return None
        now = datetime.now(timezone.utc).isoformat()
        if locked_until <= now:
            # Lock window expired — clear it so future SELECTs are clean.
            await self._db.execute(
                "UPDATE users SET failed_login_attempts = 0, "
                "locked_until = NULL WHERE id = ?", (user_id,),
            )
            await self._db.commit()
            return None
        return locked_until

    # ── Password reset ──────────────────────────────────────────────

    async def create_password_reset_token(
        self, user_id: int, ttl_hours: int = 1,
    ) -> str:
        """Mint a one-time password-reset token.

        Default TTL is 1 hour — short enough that a stolen email
        attachment doesn't grant persistent access, long enough for a
        user to act on the email when they read it.  Caller emails
        the URL; the raw token never logs.
        """
        import secrets
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)
        token = secrets.token_urlsafe(32)
        await self._db.execute(
            """INSERT INTO password_reset_tokens
               (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        await self._db.commit()
        return token

    async def consume_password_reset_token(
        self, token: str,
    ) -> Optional[int]:
        """Validate a reset token + mark it used.  Returns user_id.

        Returns None when the token is unknown, expired, or already
        used.  Does NOT change the password — caller is expected to
        call ``set_user_email_password`` with the new hash next.  The
        two operations are separate so a transport failure between
        them leaves the token used (preventing re-use) but the old
        password still working (user can request a new reset).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            "SELECT user_id, expires_at, used_at "
            "FROM password_reset_tokens WHERE token = ?", (token,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("used_at"):
            return None
        if (row.get("expires_at") or "") < now:
            return None
        user_id = int(row["user_id"])
        await self._db.execute(
            "UPDATE password_reset_tokens SET used_at = ? "
            "WHERE token = ?", (now, token),
        )
        await self._db.commit()
        return user_id

    async def mark_password_changed(self, user_id: int) -> None:
        """Stamp ``users.password_changed_at`` to now.

        Useful for the future "your password was changed at X" panel
        and for forcing other sessions to re-authenticate after a
        password change.
        """
        from datetime import datetime, timezone
        await self._db.execute(
            "UPDATE users SET password_changed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await self._db.commit()

    # ── Login attempt log ───────────────────────────────────────────

    async def record_login_attempt(
        self,
        *,
        user_id: Optional[int],
        email: Optional[str],
        success: bool,
        failure_reason: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Append one row to the login_attempts log.

        Called from every login path: on success (user_id known,
        success=True), on a wrong-password (user_id known if email
        matched, failure_reason='bad_password'), and on the no-such-
        email branch (user_id=None, failure_reason='no_such_email').

        Best-effort: a failure here is logged but never propagates —
        an audit-write failure must never block a legitimate sign-in
        or mask a legitimate 401.
        """
        from datetime import datetime, timezone
        try:
            await self._db.execute(
                """INSERT INTO login_attempts
                   (user_id, email, success, failure_reason,
                    ip_address, user_agent, attempted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, (email or "")[:200], 1 if success else 0,
                    (failure_reason or None),
                    (ip_address or "")[:64],
                    (user_agent or "")[:500],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await self._db.commit()
        except Exception as e:
            # Audit-trail bookkeeping should never break auth itself.
            import logging
            logging.getLogger("storage.users").debug(
                "record_login_attempt failed: %s", e,
            )

    async def list_my_login_attempts(
        self, user_id: int, limit: int = 20,
    ) -> list[dict]:
        """Return recent login attempts for the profile-page activity feed.

        Bounded by the ``idx_login_attempts_user`` index — page loads
        are O(log N) even on huge tables.
        """
        cur = await self._db.execute(
            "SELECT id, email, success, failure_reason, ip_address, "
            "user_agent, attempted_at FROM login_attempts "
            "WHERE user_id = ? ORDER BY attempted_at DESC LIMIT ?",
            (user_id, max(1, min(int(limit), 100))),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
