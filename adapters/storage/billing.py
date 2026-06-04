"""Billing DB operations — subscriptions and usage snapshots.

All methods are mixed into the platform DB class (DatabaseManager).
Tables: subscriptions, billing_usage_snapshots
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Pricing defaults (cents) — overridden by subscription row for grandfathered accounts
_TIER_BASE_VEHICLES = {"free": 0, "starter": 10, "pro": 10, "enterprise": 0}
_TIER_MONTHLY_BASE  = {"free": 0, "starter": 4900, "pro": 9900, "enterprise": 0}
_TIER_EXTRA_CENTS   = {"free": 0, "starter": 299,  "pro": 299,  "enterprise": 0}


class BillingMixin:
    """Billing DB helpers — mixed into DatabaseManager."""

    # Declared for mypy: provided by _DatabaseCore at runtime
    _db: Any
    _now: Any

    # ── Subscription CRUD ────────────────────────────────────────

    async def get_subscription(self, account_id: int) -> dict | None:
        """Return the subscription row for an account, or None if not found."""
        cur = await self._db.execute(
            "SELECT * FROM subscriptions WHERE account_id = ?", (account_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)

    async def get_or_create_subscription(self, account_id: int, tier: str = "free") -> dict:
        """Return existing subscription or create a stub one for the account.

        Read-modify-write wrapped in a transaction so concurrent callers
        on a pooled connection layout cannot both pass the SELECT-then-INSERT
        gap.  ON CONFLICT DO NOTHING covers the unique-key race; the
        transaction ensures the final SELECT sees the row that won.
        """
        async with self.transaction():
            sub = await self.get_subscription(account_id)
            if sub:
                return sub
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """
                INSERT INTO subscriptions
                    (account_id, tier, status,
                     base_vehicles, monthly_base_usd, extra_vehicle_cents,
                     created_at, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO NOTHING
                """,
                (
                    account_id,
                    tier,
                    _TIER_BASE_VEHICLES.get(tier, 10),
                    _TIER_MONTHLY_BASE.get(tier, 0),
                    _TIER_EXTRA_CENTS.get(tier, 0),
                    now,
                    now,
                ),
            )
            return await self.get_subscription(account_id)

    async def update_subscription(self, account_id: int, **fields) -> None:
        """Update arbitrary fields on a subscription row.

        Allowed keys: tier, status, vehicle_count, base_vehicles,
        monthly_base_usd, extra_vehicle_cents, billing_email, provider,
        provider_customer_id, provider_subscription_id, provider_data,
        trial_ends_at, current_period_start, current_period_end, canceled_at.
        """
        if not fields:
            return
        allowed = {
            "tier", "status", "vehicle_count", "base_vehicles",
            "monthly_base_usd", "extra_vehicle_cents", "billing_email",
            "provider", "provider_customer_id", "provider_subscription_id",
            "provider_base_item_id", "provider_extra_item_id",
            "provider_data", "trial_ends_at", "current_period_start",
            "current_period_end", "canceled_at", "past_due_since",
        }
        safe = {k: v for k, v in fields.items() if k in allowed}
        if not safe:
            return
        safe["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in safe)
        values = list(safe.values()) + [account_id]
        await self._db.execute(
            f"UPDATE subscriptions SET {set_clause} WHERE account_id = ?",
            values,
        )
        await self._db.commit()

    async def list_active_subscriptions(self) -> list[dict]:
        """Return all subscriptions with status 'active' or 'trialing'."""
        cur = await self._db.execute(
            "SELECT * FROM subscriptions WHERE status IN ('active','trialing')"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Webhook idempotency ──────────────────────────────────────

    async def mark_stripe_event_processed(
        self,
        event_id: str,
        event_type: str,
        account_id: int | None = None,
    ) -> bool:
        """Record a Stripe ``event.id``; return True if this is the first time.

        Stripe retries any non-2xx response and occasionally retries 2xx
        when the ack is delayed, so handlers see the same ``event.id``
        multiple times.  We INSERT-OR-IGNORE: a fresh insert means this
        invocation owns processing; a no-op insert means a prior call
        already handled it and the caller should short-circuit.
        """
        cur = await self._db.execute(
            """
            INSERT INTO processed_stripe_events (event_id, event_type, account_id)
            VALUES (?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (event_id, event_type, account_id),
        )
        await self._db.commit()
        return bool(cur.rowcount)

    # ── Usage snapshots ──────────────────────────────────────────

    async def record_usage_snapshot(
        self,
        account_id: int,
        period_start: str,
        period_end: str,
        vehicle_count: int,
        user_count: int,
        ai_queries: int,
        base_vehicles: int,
        monthly_base_cents: int,
        extra_vehicle_cents: int,
        *,
        active_vehicles: int | None = None,
        inactive_vehicles: int = 0,
    ) -> int:
        """Insert a monthly usage snapshot.  Returns the row id.

        When ``active_vehicles`` is provided the extras math uses it
        (matches what Stripe actually charges via ``sync_billing_quantity``);
        otherwise we fall back to ``vehicle_count`` for legacy callers
        that haven't migrated.  ``inactive_vehicles`` is purely
        informational and is persisted so the dashboard can render the
        "X parked — not billed" footer for historical periods too.

        Idempotent on (account_id, period_start) — replaying the job
        for the same month returns the existing row id without
        re-mutating the persisted figures.
        """
        billable = vehicle_count if active_vehicles is None else active_vehicles
        extra = max(0, billable - base_vehicles)
        amount = monthly_base_cents + extra * extra_vehicle_cents
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """
            INSERT INTO billing_usage_snapshots
                (account_id, period_start, period_end,
                 vehicle_count, active_vehicles, inactive_vehicles,
                 user_count, ai_queries,
                 extra_vehicles, amount_due_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, period_start) DO NOTHING
            """,
            (
                account_id, period_start, period_end,
                vehicle_count, billable, inactive_vehicles,
                user_count, ai_queries,
                extra, amount, now,
            ),
        )
        await self._db.commit()
        if cur.lastrowid:
            return cur.lastrowid
        # Row already existed — fetch its id
        cur2 = await self._db.execute(
            "SELECT id FROM billing_usage_snapshots WHERE account_id=? AND period_start=?",
            (account_id, period_start),
        )
        row = await cur2.fetchone()
        return row[0] if row else 0

    async def get_usage_snapshots(
        self, account_id: int, limit: int = 12
    ) -> list[dict]:
        """Return recent usage snapshots for an account (newest first)."""
        cur = await self._db.execute(
            """
            SELECT * FROM billing_usage_snapshots
            WHERE account_id = ?
            ORDER BY period_start DESC
            LIMIT ?
            """,
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_current_period_usage(self, account_id: int) -> dict | None:
        """Return the most recent usage snapshot, or None."""
        snapshots = await self.get_usage_snapshots(account_id, limit=1)
        return snapshots[0] if snapshots else None

    # ── Comp accounts ────────────────────────────────────────────
    #
    # A "comp" account gets full functionality at $0/mo for a bounded
    # window.  The window MUST have an expiration (we refuse to grant
    # without one) so a free ride doesn't become permanent by accident.
    # Renewing extends the same comp; revoking cancels it.  Every action
    # writes a row to ``comp_account_history`` so the audit trail is
    # intact for finance / support.

    async def grant_comp(
        self,
        account_id: int,
        expires_at: str,
        reason: str = "",
        actor_user_id: int | None = None,
    ) -> None:
        """Mark an account as comped through ``expires_at`` (ISO-8601 UTC).

        Raises ValueError if ``expires_at`` is empty.  Sets the comp
        flags on ``subscriptions`` and appends a ``granted`` row to the
        audit log.  Idempotent for the (account, expires_at) pair —
        calling again with the same expiry is a no-op write but still
        records a fresh audit entry so the caller can prove the action.
        """
        if not expires_at:
            raise ValueError("expires_at is required for comp grants")
        await self.get_or_create_subscription(account_id)
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            UPDATE subscriptions
               SET is_comped = 1,
                   comp_expires_at = ?,
                   comp_reason     = ?,
                   comp_granted_by = ?,
                   comp_granted_at = COALESCE(comp_granted_at, ?),
                   updated_at      = ?
             WHERE account_id = ?
            """,
            (expires_at, reason, actor_user_id, now, now, account_id),
        )
        await self._record_comp_history(
            account_id, "granted", expires_at, reason, actor_user_id
        )
        await self._db.commit()

    async def renew_comp(
        self,
        account_id: int,
        new_expires_at: str,
        reason: str = "",
        actor_user_id: int | None = None,
    ) -> None:
        """Push out the comp expiration.  Account must already be comped."""
        if not new_expires_at:
            raise ValueError("new_expires_at is required for comp renewals")
        sub = await self.get_subscription(account_id)
        if not sub or not sub.get("is_comped"):
            raise ValueError("cannot renew a comp that has not been granted")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            UPDATE subscriptions
               SET comp_expires_at = ?,
                   comp_reason     = COALESCE(NULLIF(?, ''), comp_reason),
                   updated_at      = ?
             WHERE account_id = ?
            """,
            (new_expires_at, reason, now, account_id),
        )
        await self._record_comp_history(
            account_id, "renewed", new_expires_at, reason, actor_user_id
        )
        await self._db.commit()

    async def revoke_comp(
        self,
        account_id: int,
        reason: str = "",
        actor_user_id: int | None = None,
    ) -> None:
        """Clear comp flags so the account is billed normally going forward.

        Leaves the audit row in place — the past comp window remains a
        matter of record.  The account's subscription tier is not
        changed; downgrading is a separate decision.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            UPDATE subscriptions
               SET is_comped       = 0,
                   comp_expires_at = NULL,
                   comp_granted_by = NULL,
                   comp_granted_at = NULL,
                   updated_at      = ?
             WHERE account_id = ?
            """,
            (now, account_id),
        )
        await self._record_comp_history(
            account_id, "revoked", None, reason, actor_user_id
        )
        await self._db.commit()

    async def expire_lapsed_comps(self) -> list[int]:
        """Clear ``is_comped`` for any account whose comp window has elapsed.

        Returns the list of account_ids whose comp expired.  Designed to
        be called on a scheduler tick — idempotent and cheap (indexed
        scan).  Audit rows are written so finance can correlate expired
        comps with the support tickets that follow.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """
            SELECT account_id FROM subscriptions
             WHERE is_comped = 1
               AND comp_expires_at IS NOT NULL
               AND comp_expires_at <= ?
            """,
            (now,),
        )
        ids = [int(r[0]) for r in await cur.fetchall()]
        for acct in ids:
            await self._db.execute(
                """
                UPDATE subscriptions
                   SET is_comped = 0,
                       updated_at = ?
                 WHERE account_id = ?
                """,
                (now, acct),
            )
            await self._record_comp_history(
                acct, "expired", None, "auto-expired", None
            )
        if ids:
            await self._db.commit()
        return ids

    async def record_comp_reminder_sent(
        self,
        account_id: int,
        bucket_days: int,
        expires_at: str | None = None,
    ) -> None:
        """Stamp a comp-expiry reminder into the audit log + commit.

        Used by the daily expiry sweep so each (account, bucket)
        reminder fires at most once per comp window.  The ``bucket=Nd``
        token in ``reason`` is what the throttle check greps for, so
        keep the format stable — changing it would silently double-fire
        reminders.
        """
        await self._record_comp_history(
            account_id, "expiring_reminder", expires_at,
            f"bucket={bucket_days}d", None,
        )
        await self._db.commit()

    async def get_comp_history(self, account_id: int, limit: int = 50) -> list[dict]:
        """Return the comp audit trail for an account, newest first."""
        cur = await self._db.execute(
            """
            SELECT * FROM comp_account_history
            WHERE account_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_recent_comp_actions(
        self,
        limit: int = 50,
        action: str | None = None,
    ) -> list[dict]:
        """Cross-account comp activity for the operator audit feed.

        Joined with ``accounts.name`` so the UI doesn't have to fan-out
        N lookups.  Filter by ``action`` (granted / renewed / revoked /
        expired / expiring_reminder) when set.  Newest first.
        """
        where = ""
        params: list = []
        if action:
            where = "WHERE h.action = ?"
            params.append(action)
        params.append(limit)
        cur = await self._db.execute(
            f"""
            SELECT h.*, a.name AS account_name
            FROM comp_account_history h
            JOIN accounts a ON a.id = h.account_id
            {where}
            ORDER BY h.created_at DESC, h.id DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_recently_past_due(self, days: int = 30) -> list[dict]:
        """Accounts that entered ``past_due`` within the last N days.

        Reads ``subscriptions.past_due_since`` directly — that field is
        set the first time an invoice.payment_failed lands and cleared
        on recovery, so a non-null value in the window means "this
        account is currently in the grace period (or already past it)".
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """
            SELECT s.account_id, a.name AS account_name,
                   s.tier, s.status, s.past_due_since,
                   s.provider, s.billing_email
            FROM subscriptions s
            JOIN accounts a ON a.id = s.account_id
            WHERE s.past_due_since IS NOT NULL
              AND s.past_due_since >= ?
            ORDER BY s.past_due_since DESC
            """,
            (cutoff,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_recent_processed_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict]:
        """Cross-account Stripe webhook log for the operator audit feed.

        ``processed_stripe_events`` rows include the resolved
        ``account_id`` we stamped at handler time (NULL when the event
        didn't resolve to a known account).  Join is LEFT so unmatched
        events still surface — useful when debugging "Stripe sent
        something I don't recognize".
        """
        where = ""
        params: list = []
        if event_type:
            where = "WHERE p.event_type = ?"
            params.append(event_type)
        params.append(limit)
        cur = await self._db.execute(
            f"""
            SELECT p.event_id, p.event_type, p.processed_at,
                   p.account_id, a.name AS account_name
            FROM processed_stripe_events p
            LEFT JOIN accounts a ON a.id = p.account_id
            {where}
            ORDER BY p.processed_at DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_recent_invoices_cross_account(
        self,
        *,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        account_search: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Operator-side cross-account invoice search.

        All filter args are optional; combined with AND.  Result joins
        the account name so the UI shows "PREMIER TRUCKING — $99 paid"
        rather than a bare account id.  Newest invoice first.
        """
        wheres: list[str] = []
        params: list = []
        if status:
            wheres.append("i.status = ?")
            params.append(status)
        if from_date:
            wheres.append("i.created_at >= ?")
            params.append(from_date)
        if to_date:
            wheres.append("i.created_at <= ?")
            params.append(to_date)
        if account_search:
            wheres.append("LOWER(a.name) LIKE ?")
            params.append(f"%{account_search.lower()}%")
        params.append(limit)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        cur = await self._db.execute(
            f"""
            SELECT i.*, a.name AS account_name
            FROM billing_invoices i
            JOIN accounts a ON a.id = i.account_id
            {where_sql}
            ORDER BY i.created_at DESC, i.id DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def _record_comp_history(
        self,
        account_id: int,
        action: str,
        expires_at: str | None,
        reason: str,
        actor_user_id: int | None,
    ) -> None:
        """Internal: append one row to ``comp_account_history``."""
        await self._db.execute(
            """
            INSERT INTO comp_account_history
                (account_id, action, expires_at, reason, actor_user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_id, action, expires_at, reason, actor_user_id),
        )

    # ── Enforcement ──────────────────────────────────────────────

    @staticmethod
    def is_account_blocked(
        subscription: dict | None,
        grace_period_days: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Decide whether an account should be cut off from non-billing APIs.

        Rules (in order):
          - No subscription row → not blocked (legacy / brand-new account).
          - Active comp that hasn't expired → not blocked.
          - status in ``canceled`` / ``unpaid`` → blocked immediately.
          - status == ``past_due`` and ``past_due_since + grace`` elapsed
            → blocked.  Inside the grace window the API stays open but
            the dashboard shows a warning banner.
          - Anything else (active / trialing / free) → not blocked.

        Returns ``(blocked, reason)`` where ``reason`` is a short
        machine-readable token (``""`` when not blocked).
        """
        if not subscription:
            return False, ""
        now = now or datetime.now(timezone.utc)
        if subscription.get("is_comped"):
            exp = subscription.get("comp_expires_at")
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    if exp_dt > now:
                        return False, ""
                except ValueError:
                    # Malformed timestamp — fall through to status check
                    # rather than silently letting a broken comp gate
                    # the account.
                    pass
        status = subscription.get("status", "")
        if status in ("canceled", "unpaid"):
            return True, f"subscription_{status}"
        if status == "past_due":
            since = subscription.get("past_due_since")
            if not since:
                return False, ""
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                return False, ""
            elapsed_days = (now - since_dt).total_seconds() / 86400.0
            if elapsed_days >= grace_period_days:
                return True, "past_due_grace_expired"
        return False, ""

    # ── Active-vehicle counting (billing input) ──────────────────
    #
    # "Active" = had any Samsara signal in the last N days, where
    # ``vehicle_state.captured_at`` carries the timestamp of the most
    # recent location update from Samsara.  Parked-but-connected trucks
    # keep ticking captured_at (the ELD reports periodically); trucks
    # that are sold / unplugged / in storage drop off after a few days.
    #
    # The DEFAULT_ACTIVITY_DAYS constant is the single source of truth
    # for the window length; flip it once if the policy changes.

    DEFAULT_ACTIVITY_DAYS = 3

    @staticmethod
    def _activity_cutoff_iso(days: int) -> str:
        """ISO-8601 UTC ``now - days``.  Pulled out for testability."""
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async def count_active_vehicles(
        self, account_id: int, days: int | None = None,
    ) -> int:
        """Count vehicles with a Samsara signal in the last ``days`` days.

        Uses the (account_id, captured_at) index so this stays cheap at
        any fleet size.  An empty/null ``captured_at`` (vehicle added to
        Samsara but never reported) is treated as inactive — matches the
        product rule "new trucks bill from day 1 of FIRST activity".
        """
        days = self.DEFAULT_ACTIVITY_DAYS if days is None else days
        cutoff = self._activity_cutoff_iso(days)
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM vehicle_state "
            "WHERE account_id = ? AND captured_at > ?",
            (account_id, cutoff),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_inactive_vehicles(
        self, account_id: int, days: int | None = None,
    ) -> int:
        """Count vehicles known to Samsara but silent for ``days``+ days.

        The "X inactive — not billed" footer on the billing UI sums this
        plus the count of vehicles with empty ``captured_at`` (added but
        never reported) so the operator can see exactly what 4truck
        chose not to charge them for.
        """
        days = self.DEFAULT_ACTIVITY_DAYS if days is None else days
        cutoff = self._activity_cutoff_iso(days)
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM vehicle_state "
            "WHERE account_id = ? "
            "  AND (captured_at IS NULL OR captured_at = '' OR captured_at <= ?)",
            (account_id, cutoff),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_inactive_vehicles(
        self,
        account_id: int,
        days: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return up to ``limit`` inactive vehicles for the UI footer.

        Sorted by vehicle_name so the dashboard's "not billed" list
        renders deterministically.  Only the fields the dashboard needs
        — name, last captured_at — to keep payloads small.
        """
        days = self.DEFAULT_ACTIVITY_DAYS if days is None else days
        cutoff = self._activity_cutoff_iso(days)
        cur = await self._db.execute(
            """
            SELECT vehicle_id, vehicle_name, captured_at
            FROM vehicle_state
            WHERE account_id = ?
              AND (captured_at IS NULL OR captured_at = '' OR captured_at <= ?)
            ORDER BY vehicle_name
            LIMIT ?
            """,
            (account_id, cutoff, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def compute_billing(
        self, account_id: int, days: int | None = None,
    ) -> dict:
        """Combine tier pricing with the active-vehicle count.

        This is the canonical billing math — both the API summary and
        ``sync_billing_quantity`` use it so the dashboard's preview and
        Stripe's invoice numbers stay in lockstep.  Returns:

            {
              "tier", "base_cents", "included", "extra_unit_cents",
              "active_vehicles", "inactive_vehicles",
              "extras", "extras_cents", "subtotal_cents",
            }

        Pricing comes from the subscription row (so grandfathered
        accounts keep their old plan), with a fallback to the tier
        defaults for accounts that don't have an extras price set yet.
        """
        sub = await self.get_or_create_subscription(account_id)
        tier = sub.get("tier") or "free"
        base = int(sub.get("monthly_base_usd") or 0) or _TIER_MONTHLY_BASE.get(tier, 0)
        included = int(sub.get("base_vehicles") or 0) or _TIER_BASE_VEHICLES.get(tier, 0)
        extra_unit = int(sub.get("extra_vehicle_cents") or 0) or _TIER_EXTRA_CENTS.get(tier, 0)
        active   = await self.count_active_vehicles(account_id, days)
        inactive = await self.count_inactive_vehicles(account_id, days)
        extras = max(0, active - included)
        extras_cents = extras * extra_unit
        return {
            "tier":              tier,
            "base_cents":        base,
            "included":          included,
            "extra_unit_cents":  extra_unit,
            "active_vehicles":   active,
            "inactive_vehicles": inactive,
            "extras":            extras,
            "extras_cents":      extras_cents,
            "subtotal_cents":    base + extras_cents,
        }

    # ── Invoices ─────────────────────────────────────────────────

    _INVOICE_FIELDS = (
        "provider_subscription_id", "provider_customer_id",
        "amount_due_cents", "amount_paid_cents", "currency",
        "status", "period_start", "period_end",
        "hosted_invoice_url", "invoice_pdf_url", "paid_at",
    )

    async def record_invoice(
        self,
        account_id: int,
        provider_invoice_id: str,
        **fields,
    ) -> int:
        """Upsert a Stripe invoice mirror; returns the row id.

        The first call inserts; subsequent calls for the same
        ``provider_invoice_id`` (e.g. payment_succeeded after open) update
        the mutable fields so the local mirror tracks Stripe's view.
        Unknown field names are silently dropped so a Stripe SDK version
        bump that adds new attributes doesn't crash the handler.
        """
        clean = {k: v for k, v in fields.items() if k in self._INVOICE_FIELDS}
        cols = ["account_id", "provider_invoice_id", *clean.keys()]
        placeholders = ", ".join(["?"] * len(cols))
        # Build an upsert that refreshes the mutable columns on conflict
        # — invoices transition open → paid → uncollectible; we want the
        # latest values without losing the row.
        updates = ", ".join(f"{k}=excluded.{k}" for k in clean.keys())
        on_conflict = (
            f"ON CONFLICT(provider_invoice_id) DO UPDATE SET {updates}"
            if updates else
            "ON CONFLICT(provider_invoice_id) DO NOTHING"
        )
        params = [account_id, provider_invoice_id, *clean.values()]
        cur = await self._db.execute(
            f"""
            INSERT INTO billing_invoices ({", ".join(cols)})
            VALUES ({placeholders})
            {on_conflict}
            """,
            params,
        )
        await self._db.commit()
        if cur.lastrowid:
            return cur.lastrowid
        # Row already existed — fetch its id
        cur2 = await self._db.execute(
            "SELECT id FROM billing_invoices WHERE provider_invoice_id=?",
            (provider_invoice_id,),
        )
        row = await cur2.fetchone()
        return row[0] if row else 0

    async def get_invoices(
        self, account_id: int, limit: int = 24
    ) -> list[dict]:
        """Return invoices for an account, newest first."""
        cur = await self._db.execute(
            """
            SELECT * FROM billing_invoices
            WHERE account_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def find_account_by_stripe_customer(
        self, customer_id: str
    ) -> int | None:
        """Resolve a Stripe ``customer.id`` back to one of our accounts.

        Used by webhook handlers for events whose payload doesn't carry
        our ``metadata.account_id`` (invoices, in particular — Stripe
        generates them from the subscription and we never get a chance
        to attach metadata).
        """
        if not customer_id:
            return None
        cur = await self._db.execute(
            "SELECT account_id FROM subscriptions WHERE provider_customer_id = ?",
            (customer_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def find_account_by_stripe_subscription(
        self, subscription_id: str
    ) -> int | None:
        """Resolve a Stripe ``subscription.id`` back to one of our accounts."""
        if not subscription_id:
            return None
        cur = await self._db.execute(
            "SELECT account_id FROM subscriptions WHERE provider_subscription_id = ?",
            (subscription_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None


    # ── Pricing helpers ──────────────────────────────────────────

    @staticmethod
    def compute_amount_due(
        vehicle_count: int,
        base_vehicles: int,
        monthly_base_cents: int,
        extra_vehicle_cents: int,
    ) -> tuple[int, int]:
        """Return (extra_vehicle_count, total_cents)."""
        extra = max(0, vehicle_count - base_vehicles)
        total = monthly_base_cents + extra * extra_vehicle_cents
        return extra, total

    @staticmethod
    def tier_pricing(tier: str) -> dict:
        """Return the default pricing dict for a tier."""
        return {
            "tier": tier,
            "base_vehicles": _TIER_BASE_VEHICLES.get(tier, 0),
            "monthly_base_cents": _TIER_MONTHLY_BASE.get(tier, 0),
            "extra_vehicle_cents": _TIER_EXTRA_CENTS.get(tier, 0),
        }

    @staticmethod
    def build_summary(
        sub: dict,
        *,
        provider: str = "stub",
        active_vehicles: int | None = None,
        inactive_vehicles: int = 0,
        inactive_sample: list[dict] | None = None,
    ) -> dict:
        """Project a subscription row into the dashboard's billing summary.

        Both providers share this so the comp-account math (itemized
        line items + "100% Special Discount" + $0 total) and the
        active-vehicle billing math live in one place.

        When ``active_vehicles`` is supplied, billing is driven by it
        (the new model: charge only for trucks signaling in last N
        days).  When it's None we fall back to ``sub["vehicle_count"]``
        — the legacy raw count — so tests and call sites that haven't
        migrated keep working.

        ``inactive_vehicles`` / ``inactive_sample`` are surfaced for the
        "X inactive — not billed" UI footer; they don't affect the math.

        The line items are intentionally returned even for comped
        accounts — operators should see "what 4truck is covering" so
        a comp feels like a granted benefit, not free air.
        """
        billable = (
            active_vehicles
            if active_vehicles is not None
            else int(sub.get("vehicle_count") or 0)
        )
        extra, subtotal = BillingMixin.compute_amount_due(
            vehicle_count=billable,
            base_vehicles=sub["base_vehicles"],
            monthly_base_cents=sub["monthly_base_usd"],
            extra_vehicle_cents=sub["extra_vehicle_cents"],
        )
        tier = sub.get("tier", "free")
        line_items: list[dict] = []
        if sub["monthly_base_usd"] > 0:
            line_items.append({
                "label": f"{tier.capitalize()} plan ({sub['base_vehicles']} trucks included)",
                "amount_cents": sub["monthly_base_usd"],
            })
        if extra > 0:
            line_items.append({
                "label": f"{extra} extra truck{'s' if extra != 1 else ''} × ${sub['extra_vehicle_cents'] / 100:.2f}",
                "amount_cents": extra * sub["extra_vehicle_cents"],
            })
        is_comped = bool(sub.get("is_comped"))
        if is_comped and subtotal > 0:
            line_items.append({
                "label": "100% Special Discount",
                "amount_cents": -subtotal,
            })
        discount = subtotal if is_comped else 0
        amount_due = 0 if is_comped else subtotal
        return {
            "tier":                 tier,
            "status":               sub["status"],
            "vehicle_count":        sub["vehicle_count"],
            "active_vehicles":      billable,
            "inactive_vehicles":    inactive_vehicles,
            "inactive_sample":      inactive_sample or [],
            "base_vehicles":        sub["base_vehicles"],
            "monthly_base_cents":   sub["monthly_base_usd"],
            "extra_vehicle_cents":  sub["extra_vehicle_cents"],
            "extra_vehicles":       extra,
            "subtotal_cents":       subtotal,
            "discount_cents":       discount,
            "amount_due_cents":     amount_due,
            "line_items":           line_items,
            "is_comped":            is_comped,
            "comp_expires_at":      sub.get("comp_expires_at"),
            "comp_reason":          sub.get("comp_reason", ""),
            "billing_email":        sub["billing_email"],
            "provider":             provider,
            "current_period_start": sub.get("current_period_start"),
            "current_period_end":   sub.get("current_period_end"),
            "trial_ends_at":        sub.get("trial_ends_at"),
            "past_due_since":       sub.get("past_due_since"),
        }

    async def get_billing_summary(
        self,
        account_id: int,
        *,
        provider: str = "stub",
        days: int | None = None,
        inactive_sample_limit: int = 10,
    ) -> dict:
        """End-to-end summary: subscription + active-vehicle math + comp.

        This is what both providers' ``get_summary`` should call.  It
        composes the pure projection (``build_summary``) with the
        activity-driven inputs (``count_active_vehicles`` /
        ``count_inactive_vehicles`` / ``list_inactive_vehicles``) so
        the dashboard sees a single, consistent dict whether the
        deployment is stub or Stripe.
        """
        sub = await self.get_or_create_subscription(account_id)
        active = await self.count_active_vehicles(account_id, days=days)
        inactive = await self.count_inactive_vehicles(account_id, days=days)
        sample = await self.list_inactive_vehicles(
            account_id, days=days, limit=inactive_sample_limit,
        )
        return self.build_summary(
            sub,
            provider=provider,
            active_vehicles=active,
            inactive_vehicles=inactive,
            inactive_sample=sample,
        )
