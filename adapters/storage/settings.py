"""Account settings, digest subscriptions, AI usage, and audit log CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


class SettingsMixin:

    # ── Account Settings (key-value) ──────────────────────────────

    # well-known setting keys. Strings are
    # centralised here so the API, dashboard, and bot all reference the
    # same canonical name (typo-proof).
    KEY_SCORECARD_DEFAULT_SUBJECT = "scorecard_default_subject"
    KEY_SCORECARD_PILLAR_CAPS     = "scorecard_pillar_caps"       # JSON: {"safety":50,"efficiency":25,"compliance":25}
    KEY_FORUM_INCLUDE_AI_NOTE     = "forum_include_ai_note"       # "1" / "0" — append AI Diagnosis section to group-routed alerts

    async def get_account_setting(self, account_id: int, key: str,
                                  default: str = "") -> str:
        """Get a single setting value for an account."""
        cur = await self._db.execute(
            "SELECT value FROM account_settings "
            "WHERE account_id = ? AND key = ?",
            (account_id, key),
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_account_setting(self, account_id: int, key: str,
                                  value: str):
        """Set a single setting value for an account (upsert)."""
        await self._db.execute(
            "INSERT INTO account_settings (account_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, key) DO UPDATE SET value = ?, updated_at = ?",
            (account_id, key, value, self._now(), value, self._now()),
        )
        await self._db.commit()

    # ── Digest Subscriptions ──────────────────────────────────────

    async def subscribe_digest(
        self, user_id: int, frequency: str = "daily", send_hour: int = 7,
    ) -> None:
        now = self._now()
        await self._db.execute(
            """INSERT INTO digest_subscriptions (user_id, frequency, send_hour, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET frequency = excluded.frequency,
                   send_hour = excluded.send_hour,
                   is_active = 1""",
            (user_id, frequency, send_hour, now),
        )
        await self._db.commit()

    async def unsubscribe_digest(self, user_id: int) -> None:
        await self._db.execute(
            "UPDATE digest_subscriptions SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )
        await self._db.commit()

    async def get_digest_subscription(self, user_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM digest_subscriptions WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_digest_subscribers(self, send_hour: int, frequency: str = "daily") -> list[dict]:
        """Get all active subscribers for a given hour and frequency."""
        cur = await self._db.execute(
            """SELECT ds.*, u.telegram_id, u.account_id, u.role, u.truck_num
               FROM digest_subscriptions ds
               JOIN users u ON u.id = ds.user_id
               WHERE ds.is_active = 1 AND ds.send_hour = ? AND ds.frequency = ?
               AND u.is_active = 1""",
            (send_hour, frequency),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def subscribe_digest_ext(
        self, user_id: int, frequency: str = "daily",
        send_hour: int = 7, timezone: str = "America/New_York",
        report_type: str = "faults",
    ) -> None:
        """Subscribe to auto reports with timezone and report type support."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO digest_subscriptions
               (user_id, frequency, send_hour, timezone, report_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET frequency = excluded.frequency,
                   send_hour = excluded.send_hour,
                   timezone = excluded.timezone,
                   report_type = excluded.report_type,
                   is_active = 1""",
            (user_id, frequency, send_hour, timezone, report_type, now),
        )
        await self._db.commit()

    async def get_digest_subscribers_by_local_hour(self, utc_hour: int) -> list[dict]:
        """Get all active digest subscribers whose local send_hour matches now.

        Computes which UTC hour each subscriber's local send_hour maps to,
        and returns those matching the given utc_hour.
        """
        # Fetch all active subscriptions with user info
        cur = await self._db.execute(
            """SELECT ds.*, u.telegram_id, u.account_id, u.role, u.truck_num
               FROM digest_subscriptions ds
               JOIN users u ON u.id = ds.user_id
               WHERE ds.is_active = 1 AND u.is_active = 1""",
        )
        rows = await cur.fetchall()

        from datetime import datetime as _dt, timezone as _tz
        from zoneinfo import ZoneInfo

        now_utc = _dt.now(_tz.utc)
        results = []
        for r in rows:
            row = dict(r)
            tz_name = row.get("timezone", "America/New_York")
            send_hour = row.get("send_hour", 7)
            try:
                user_tz = ZoneInfo(tz_name)
                # Create a datetime at the user's desired local send_hour today
                local_now = now_utc.astimezone(user_tz)
                local_send = local_now.replace(hour=send_hour, minute=0, second=0, microsecond=0)
                # Convert that to UTC and check if the UTC hour matches
                utc_send = local_send.astimezone(_tz.utc)
                if utc_send.hour == utc_hour:
                    results.append(row)
            except Exception:
                # Fallback: treat send_hour as UTC
                if send_hour == utc_hour:
                    results.append(row)
        return results

    # ── Audit Log ─────────────────────────────────────────────────

    async def add_audit_log(
        self, account_id: int, user_id: Optional[int],
        action: str, target_type: str = "", target_id: str = "",
        details: str = "",
    ) -> int:
        """Record an action in the audit log."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO audit_log
               (account_id, user_id, action, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, action, target_type, target_id, details, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_audit_log(self, account_id: int, limit: int = 50) -> list[dict]:
        """Get recent audit log entries for an account."""
        cur = await self._db.execute(
            "SELECT * FROM audit_log WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── AI Usage Tracking ─────────────────────────────────────────

    async def log_ai_usage(
        self, account_id: int, user_id: int, model: str,
        request_type: str, prompt_tokens: int = 0,
        reply_tokens: int = 0, total_tokens: int = 0,
        thinking_tokens: int = 0,
    ) -> int:
        """Log an AI API call with token counts."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO ai_usage
               (account_id, user_id, model, request_type,
                prompt_tokens, reply_tokens, thinking_tokens,
                total_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, model, request_type,
             prompt_tokens, reply_tokens, thinking_tokens,
             total_tokens, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_ai_usage_stats(self, account_id: int, days: int = 30) -> dict:
        """Get AI usage stats for an account over the past N days.

        Returns dict with total_requests, total_tokens, by_type breakdown,
        and by_model breakdown.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """SELECT request_type, model,
                      COUNT(*) as cnt,
                      SUM(prompt_tokens) as sum_prompt,
                      SUM(reply_tokens) as sum_reply,
                      SUM(COALESCE(thinking_tokens, 0)) as sum_thinking,
                      SUM(total_tokens) as sum_total
               FROM ai_usage
               WHERE account_id = ? AND created_at >= ?
               GROUP BY request_type, model""",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        by_type: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_requests = 0
        total_tokens = 0
        for r in rows:
            rt = r["request_type"]
            m = r["model"]
            cnt = r["cnt"]
            tok = r["sum_total"] or 0
            total_requests += cnt
            total_tokens += tok
            # Aggregate by type
            if rt not in by_type:
                by_type[rt] = {"requests": 0, "tokens": 0}
            by_type[rt]["requests"] += cnt
            by_type[rt]["tokens"] += tok
            # Aggregate by model
            if m not in by_model:
                by_model[m] = {"requests": 0, "tokens": 0,
                               "prompt_tokens": 0, "reply_tokens": 0,
                               "thinking_tokens": 0}
            by_model[m]["requests"] += cnt
            by_model[m]["tokens"] += tok
            by_model[m]["prompt_tokens"] += r["sum_prompt"] or 0
            by_model[m]["reply_tokens"] += r["sum_reply"] or 0
            by_model[m]["thinking_tokens"] += r["sum_thinking"] or 0
        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "by_type": by_type,
            "by_model": by_model,
            "days": days,
        }

    async def get_ai_usage_daily(self, account_id: int, days: int = 7) -> list[dict]:
        """Get daily AI usage breakdown for the past N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """SELECT DATE(created_at) as day,
                      COUNT(*) as requests,
                      SUM(total_tokens) as tokens
               FROM ai_usage
               WHERE account_id = ? AND created_at >= ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_ai_usage_all_accounts(self, days: int = 90) -> dict:
        """Get AI usage stats across ALL accounts for sysowner dashboard.

        Returns dict with totals, per-account breakdown, per-model breakdown,
        per-type breakdown, and daily aggregates.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Per-account + model + type
        cur = await self._db.execute(
            """SELECT u.account_id, COALESCE(a.name, 'system') as acct_name,
                      u.request_type, u.model,
                      COUNT(*) as cnt,
                      SUM(u.prompt_tokens) as sum_prompt,
                      SUM(u.reply_tokens) as sum_reply,
                      SUM(COALESCE(u.thinking_tokens, 0)) as sum_thinking,
                      SUM(u.total_tokens) as sum_total
               FROM ai_usage u
               LEFT JOIN accounts a ON a.id = u.account_id
               WHERE u.created_at >= ?
               GROUP BY u.account_id, u.request_type, u.model
               ORDER BY sum_total DESC""",
            (cutoff,),
        )
        rows = await cur.fetchall()

        totals = {"requests": 0, "tokens": 0, "prompt": 0, "reply": 0,
                  "thinking": 0}
        by_account: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        by_type: dict[str, dict] = {}

        for r in rows:
            acct = r["acct_name"] or f"acct#{r['account_id']}"
            rt = r["request_type"]
            m = r["model"]
            cnt = r["cnt"]
            pt = r["sum_prompt"] or 0
            rp = r["sum_reply"] or 0
            th = r["sum_thinking"] or 0
            tok = r["sum_total"] or 0

            totals["requests"] += cnt
            totals["tokens"] += tok
            totals["prompt"] += pt
            totals["reply"] += rp
            totals["thinking"] += th

            if acct not in by_account:
                by_account[acct] = {"requests": 0, "tokens": 0,
                                    "prompt": 0, "reply": 0,
                                    "thinking": 0, "models": {}}
            by_account[acct]["requests"] += cnt
            by_account[acct]["tokens"] += tok
            by_account[acct]["prompt"] += pt
            by_account[acct]["reply"] += rp
            by_account[acct]["thinking"] += th
            if m not in by_account[acct]["models"]:
                by_account[acct]["models"][m] = {"requests": 0, "tokens": 0,
                                                  "prompt": 0, "reply": 0,
                                                  "thinking": 0}
            by_account[acct]["models"][m]["requests"] += cnt
            by_account[acct]["models"][m]["tokens"] += tok
            by_account[acct]["models"][m]["prompt"] += pt
            by_account[acct]["models"][m]["reply"] += rp
            by_account[acct]["models"][m]["thinking"] += th

            if m not in by_model:
                by_model[m] = {"requests": 0, "tokens": 0,
                               "prompt": 0, "reply": 0, "thinking": 0}
            by_model[m]["requests"] += cnt
            by_model[m]["tokens"] += tok
            by_model[m]["prompt"] += pt
            by_model[m]["reply"] += rp
            by_model[m]["thinking"] += th

            if rt not in by_type:
                by_type[rt] = {"requests": 0, "tokens": 0}
            by_type[rt]["requests"] += cnt
            by_type[rt]["tokens"] += tok

        # Daily totals (last 30 days max for display)
        daily_cutoff = (datetime.now(timezone.utc) - timedelta(
            days=min(days, 30))).isoformat()
        cur = await self._db.execute(
            """SELECT DATE(created_at) as day,
                      COUNT(*) as requests,
                      SUM(total_tokens) as tokens,
                      SUM(prompt_tokens) as prompt,
                      SUM(reply_tokens) as reply
               FROM ai_usage
               WHERE created_at >= ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (daily_cutoff,),
        )
        daily = [dict(r) for r in await cur.fetchall()]

        return {
            "days": days,
            "totals": totals,
            "by_account": by_account,
            "by_model": by_model,
            "by_type": by_type,
            "daily": daily,
        }
