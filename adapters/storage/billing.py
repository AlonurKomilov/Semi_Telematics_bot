"""Billing DB operations — subscriptions and usage snapshots.

All methods are mixed into the platform DB class (DatabaseManager).
Tables: subscriptions, billing_usage_snapshots
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
        """Return existing subscription or create a stub one for the account."""
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
        await self._db.commit()
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
            "provider_data", "trial_ends_at", "current_period_start",
            "current_period_end", "canceled_at",
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
    ) -> int:
        """Insert a monthly usage snapshot.  Returns the row id.

        Idempotent: if a row for (account_id, period_start) already exists
        the existing row id is returned.
        """
        extra = max(0, vehicle_count - base_vehicles)
        amount = monthly_base_cents + extra * extra_vehicle_cents
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """
            INSERT INTO billing_usage_snapshots
                (account_id, period_start, period_end,
                 vehicle_count, user_count, ai_queries,
                 extra_vehicles, amount_due_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, period_start) DO NOTHING
            """,
            (
                account_id, period_start, period_end,
                vehicle_count, user_count, ai_queries,
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
