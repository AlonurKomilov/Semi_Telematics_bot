"""``plan_price_rollouts`` — a price change reaching the accounts already on
a plan, one Stripe subscription at a time, with a row per outcome.

A rollout is the money event with a blast radius: it is previewed, it is
resumable (an item row with a terminal outcome is never re-run), and it
is never enumerated from Stripe — the candidates are OUR subscription
rows.  The engine that drives Stripe lives in
capabilities/platform/billing/rollout.py; this mixin only remembers.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

#: a subscription Stripe is still charging (or about to)
LIVE_STATUSES = ("active", "trialing", "past_due")
#: outcomes after which an item is never re-run in the same rollout
TERMINAL_OUTCOMES = ("changed", "already", "skipped_status", "has_schedule", "no_base_item")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanRolloutsMixin:
    """Price rollouts — mixed into the platform DB class."""

    async def subscriptions_on_tier(self, tier: str) -> list[dict]:
        """The subscription rows a price rollout must reach: on *tier*,
        billed by Stripe, with a Stripe subscription id, still live."""
        marks = ",".join("?" for _ in LIVE_STATUSES)
        # a comped account keeps its Stripe subscription as it is: the comp is
        # the local overlay that says what it pays, and a rollout must not
        # change what Stripe bills it (the same exemption the operator's
        # plan move makes)
        cur = await self._db.execute(
            "SELECT * FROM subscriptions WHERE tier = ? AND provider = 'stripe' "
            f"AND provider_subscription_id <> '' AND status IN ({marks}) "
            "AND COALESCE(is_comped, 0) = 0 ORDER BY account_id",
            (tier, *LIVE_STATUSES),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def create_price_rollout(
        self, tier: str, *, from_price_id: str, to_price_id: str,
        from_cents: int, to_cents: int, actor: str,
    ) -> int:
        """Open a rollout — or, when another operator opened one for this
        plan a moment ago (the partial unique index refuses a second open
        row), return that one's id: two operators join one rollout."""
        try:
            cur = await self._db.execute(
                "INSERT INTO plan_price_rollouts (tier, from_price_id, to_price_id, from_cents, to_cents, "
                "actor, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tier, from_price_id, to_price_id, int(from_cents), int(to_cents), actor, _now()),
            )
            await self._db.commit()
            return int(cur.lastrowid)
        except Exception:
            try:
                await self._db.rollback()
            except Exception:
                # a rollback that itself fails leaves nothing to undo: the insert
                # never committed, and the open-rollout read below is the answer
                logger.debug("rollback after a refused rollout insert failed", exc_info=True)
            existing = await self.open_price_rollout(tier)
            if existing:
                return int(existing["id"])
            raise

    async def claim_rollout(self, rollout_id: int, token: str, *, stale_minutes: int = 5) -> bool:
        """Take the rollout for one batch: an atomic compare-and-set on the
        row.  Held by another live batch → False (RolloutBusy); a claim
        older than *stale_minutes* is a crashed batch and is taken over."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
        await self._db.execute(
            "UPDATE plan_price_rollouts SET running_token = ?, running_since = ? "
            "WHERE id = ? AND finished_at IS NULL AND (running_token = '' OR running_since < ?)",
            (token, _now(), int(rollout_id), cutoff))
        await self._db.commit()
        cur = await self._db.execute("SELECT running_token FROM plan_price_rollouts WHERE id = ?", (int(rollout_id),))
        r = await cur.fetchone()
        return bool(r) and r["running_token"] == token

    async def release_rollout(self, rollout_id: int, token: str) -> None:
        """The batch is over: the claim is dropped and the heartbeat set,
        only by the batch that holds it."""
        await self._db.execute(
            "UPDATE plan_price_rollouts SET running_token = '', running_since = '', last_batch_at = ? "
            "WHERE id = ? AND running_token = ?", (_now(), int(rollout_id), token))
        await self._db.commit()

    async def open_price_rollout(self, tier: str) -> Optional[dict]:
        """The unfinished rollout for *tier*, if any (there is at most one)."""
        cur = await self._db.execute(
            "SELECT * FROM plan_price_rollouts WHERE tier = ? AND finished_at IS NULL "
            "ORDER BY id DESC LIMIT 1", (tier,))
        r = await cur.fetchone()
        return dict(r) if r else None

    async def abort_stale_rollouts(self, tier: str, *, older_than_minutes: int = 15) -> int:
        """An unfinished rollout nobody has touched for a while is a crashed
        one: mark it aborted so a new one may start.  Returns how many."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat()
        # "untouched for a while" is measured from the LAST batch, not the
        # first: a long rollout resumed every few seconds is not stale
        cur = await self._db.execute(
            "SELECT id FROM plan_price_rollouts WHERE tier = ? AND finished_at IS NULL "
            "AND running_token = '' AND COALESCE(NULLIF(last_batch_at, ''), started_at) < ?",
            (tier, cutoff))
        ids = [int(r["id"]) for r in await cur.fetchall()]
        for rid in ids:
            await self._db.execute(
                "UPDATE plan_price_rollouts SET finished_at = ?, aborted = 1 WHERE id = ?", (_now(), rid))
        if ids:
            await self._db.commit()
        return len(ids)

    async def record_rollout_item(
        self, rollout_id: int, account_id: int, *, subscription_id: str,
        outcome: str, error: str = "", effective_at: str = "",
    ) -> None:
        await self._db.execute(
            "INSERT INTO plan_price_rollout_items (rollout_id, account_id, subscription_id, outcome, error, "
            "effective_at, at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(rollout_id, account_id) DO UPDATE SET subscription_id = excluded.subscription_id, "
            "outcome = excluded.outcome, error = excluded.error, effective_at = excluded.effective_at, at = excluded.at",
            (int(rollout_id), int(account_id), subscription_id, outcome, (error or "")[:500],
             effective_at or "", _now()),
        )
        await self._db.commit()

    async def rollout_items(self, rollout_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM plan_price_rollout_items WHERE rollout_id = ? ORDER BY account_id", (int(rollout_id),))
        return [dict(r) for r in await cur.fetchall()]

    async def finish_price_rollout(self, rollout_id: int, *, aborted: bool = False, summary: Optional[dict] = None) -> None:
        await self._db.execute(
            "UPDATE plan_price_rollouts SET finished_at = ?, aborted = ?, summary = ? WHERE id = ?",
            (_now(), 1 if aborted else 0, json.dumps(summary or {}), int(rollout_id)))
        await self._db.commit()

    async def list_price_rollouts(self, tier: str, *, limit: int = 10) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM plan_price_rollouts WHERE tier = ? ORDER BY id DESC LIMIT ?", (tier, int(limit)))
        out = []
        for r in await cur.fetchall():
            d = dict(r)
            try:
                d["summary"] = json.loads(d.get("summary") or "{}")
            except (TypeError, ValueError):
                d["summary"] = {}
            out.append(d)
        return out
