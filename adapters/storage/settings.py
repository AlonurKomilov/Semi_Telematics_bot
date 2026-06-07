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

    # ── Scheduled Reports (legacy table name: digest_subscriptions) ──

    async def unsubscribe_digest(
        self, user_id: int, report_type: Optional[str] = None,
    ) -> None:
        """Deactivate a Scheduled Report row.

        ``report_type=None`` (the default, pre-2026-06 behaviour)
        deactivates every schedule the user has — used when an admin
        revokes ``can_digest`` or the user clicks "Stop all" in the bot
        wizard.

        ``report_type="faults"`` deactivates only the faults schedule —
        used by the dashboard's per-row delete button and the bot
        wizard's per-schedule "Stop" action.
        """
        if report_type is None:
            await self._db.execute(
                "UPDATE digest_subscriptions SET is_active = 0 WHERE user_id = ?",
                (user_id,),
            )
        else:
            await self._db.execute(
                "UPDATE digest_subscriptions SET is_active = 0 "
                "WHERE user_id = ? AND report_type = ?",
                (user_id, report_type),
            )
        await self._db.commit()

    async def get_digest_subscription(self, user_id: int) -> Optional[dict]:
        """Return ANY active schedule for the user, or None.

        Single-row accessor — kept for backward compat with callers
        that expect "one schedule per user" (the pre-2026-06 model).
        New code should call :py:meth:`get_digest_subscriptions`
        instead so it sees the full list.
        """
        cur = await self._db.execute(
            "SELECT * FROM digest_subscriptions "
            "WHERE user_id = ? AND is_active = 1 "
            "ORDER BY id LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_digest_subscriptions(self, user_id: int) -> list[dict]:
        """Return every active schedule for the user, ordered by id.

        The multi-schedule entrypoint — one row per (user, report_type).
        Empty list when the user has no schedules.
        """
        cur = await self._db.execute(
            "SELECT * FROM digest_subscriptions "
            "WHERE user_id = ? AND is_active = 1 "
            "ORDER BY id",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

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
        delivery_channels: Optional[list[str]] = None,
    ) -> None:
        """Upsert a Scheduled Reports row with timezone, report type,
        and delivery channels.

        ``delivery_channels`` is a list of channel names — currently
        ``"telegram"`` and/or ``"email"``.  Defaults to ``["telegram"]``
        to preserve pre-2026-06 behaviour.  Stored as a comma-separated
        TEXT column; the bot scheduler splits + dispatches per channel.
        """
        now = self._now()
        channels = ",".join(delivery_channels) if delivery_channels else "telegram"
        # ON CONFLICT key is (user_id, report_type) — multi-schedule
        # model added 2026-06.  A user can stack Faults Daily + Fuel
        # Weekly + Health Monthly as three independent rows; saving
        # the same (user, report_type) twice updates the existing row.
        await self._db.execute(
            """INSERT INTO digest_subscriptions
               (user_id, frequency, send_hour, timezone, report_type,
                delivery_channels, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, report_type) DO UPDATE
               SET frequency = excluded.frequency,
                   send_hour = excluded.send_hour,
                   timezone = excluded.timezone,
                   delivery_channels = excluded.delivery_channels,
                   is_active = 1""",
            (user_id, frequency, send_hour, timezone, report_type, channels, now),
        )
        await self._db.commit()

    async def get_digest_subscribers_by_local_hour(self, utc_hour: int) -> list[dict]:
        """Get all active digest subscribers whose local send_hour matches now.

        Computes which UTC hour each subscriber's local send_hour maps to,
        and returns those matching the given utc_hour.
        """
        # Fetch all active subscriptions with user info.  Includes
        # ``email`` + ``email_verified`` so the scheduler can dispatch
        # the email-delivery branch (added 2026-06) without a second
        # round-trip per subscriber.
        cur = await self._db.execute(
            """SELECT ds.*,
                      u.telegram_id, u.account_id, u.role, u.truck_num,
                      u.email, u.email_verified, u.display_name
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
        *,
        latency_ms: int | None = None,
        error_type: str | None = None,
        tool_success_count: int | None = None,
        role: str | None = None,
        prompt_category: str | None = None,
    ) -> int:
        """Log an AI API call with token counts and router telemetry.

        Failure rows (no usage data) are logged too, with token counts
        zeroed and ``error_type`` set — the model router needs both
        sides of the ratio (successes / failures per model) to compute
        an error rate that isn't always 0 %.

        ``role`` is the caller's effective role at request time
        ('owner', 'dispatcher', 'driver', …).  Different roles ask
        different question shapes; the router scores per (model x role)
        so a model great for drivers but weak for owner briefings
        doesn't get promoted across the board.

        ``prompt_category`` sub-classifies free-form questions
        (lookup / analysis / comparison / summary / troubleshooting /
        other) so the router can pick the model that's historically
        best for *this kind* of question.  Null for non-question
        actions (summary / diagnosis already carry their shape in
        request_type).
        """
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO ai_usage
               (account_id, user_id, model, request_type,
                prompt_tokens, reply_tokens, thinking_tokens,
                total_tokens, latency_ms, error_type,
                tool_success_count, role, prompt_category, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, model, request_type,
             prompt_tokens, reply_tokens, thinking_tokens,
             total_tokens, latency_ms, error_type,
             tool_success_count, role, prompt_category, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_ai_model_scores(
        self,
        account_id: int,
        *,
        role: str | None = None,
        request_type: str | None = None,
        prompt_category: str | None = None,
        since_days: int = 7,
        candidates: list[str] | None = None,
    ) -> dict[str, dict]:
        """Score each model by rolling availability + speed + tool success.

        Returns ``{model: {score, samples, error_rate, p50_latency_ms,
        tool_success_rate}}``.  Score is in [0, 1]; higher is better.
        The router picks the highest-scored model from the candidate
        set (typically a tier's fallback chain) with an ε-greedy
        explore step.

        Filters:
        - ``role``: score per (model x role) so the router can pick
          per-role winners.  Pass None to score across roles.
        - ``request_type``: same idea for action ('question', 'summary',
          'diagnosis', 'chat', …).
        - ``prompt_category``: sub-classification of free-form
          questions ('lookup', 'analysis', 'comparison', 'summary',
          'troubleshooting', 'other').  Lets the router pick the model
          best for *this kind* of question — e.g. a Fast-tier model
          that wins at lookups but loses at analysis stays the lookup
          winner without being misranked overall.
        - ``candidates``: restrict to this set of model names; absent
          ones are returned with ``samples=0`` so the router can spot
          unobserved candidates and explore them.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        where = ["account_id = ?", "created_at >= ?"]
        params: list = [account_id, cutoff]
        if role:
            where.append("role = ?")
            params.append(role)
        if request_type:
            where.append("request_type = ?")
            params.append(request_type)
        if prompt_category:
            where.append("prompt_category = ?")
            params.append(prompt_category)

        sql = (
            "SELECT model, "
            "       COUNT(*) AS samples, "
            "       SUM(CASE WHEN COALESCE(error_type, 'ok') = 'ok' "
            "                THEN 0 ELSE 1 END) AS errors, "
            # AVG over only non-null latencies — errors that happened
            # before we even started the call have no latency.
            "       AVG(CASE WHEN latency_ms IS NOT NULL "
            "                THEN latency_ms END) AS avg_latency_ms, "
            "       SUM(COALESCE(tool_success_count, 0)) AS tool_successes, "
            "       SUM(CASE WHEN tool_success_count IS NOT NULL "
            "                THEN 1 ELSE 0 END) AS tool_turns "
            f"FROM ai_usage WHERE {' AND '.join(where)} "
            "GROUP BY model"
        )
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()

        scored: dict[str, dict] = {}
        for r in rows:
            m = r["model"]
            samples = int(r["samples"] or 0)
            errors = int(r["errors"] or 0)
            avg_lat = float(r["avg_latency_ms"] or 0)
            tool_successes = int(r["tool_successes"] or 0)
            tool_turns = int(r["tool_turns"] or 0)
            scored[m] = _compute_score(
                samples=samples, errors=errors,
                avg_latency_ms=avg_lat,
                tool_successes=tool_successes,
                tool_turns=tool_turns,
            )

        # Surface unobserved candidates so the router knows to explore them.
        if candidates:
            for m in candidates:
                if m not in scored:
                    scored[m] = _compute_score(
                        samples=0, errors=0,
                        avg_latency_ms=0.0,
                        tool_successes=0, tool_turns=0,
                    )
        return scored


def _compute_score(
    *,
    samples: int,
    errors: int,
    avg_latency_ms: float,
    tool_successes: int,
    tool_turns: int,
) -> dict:
    """Combine availability + speed + tool success into one [0, 1] score.

    Score weights:
    - 60 % availability (1 - error_rate).  An unreachable model is useless.
    - 25 % speed (normalized — sub-2s = full credit, 20s+ = zero).
    - 15 % tool success rate (only for turns that actually called tools).

    Tiny sample sets (n < 5) get a default mid-score so the router
    doesn't lock onto a model with one lucky data point.  ε-greedy
    explore handles the genuine cold start.
    """
    if samples < 5:
        # Not enough data — return a neutral score that lets static
        # ordering win, but include the sample count so the caller can
        # see it's cold-start data.
        return {
            "score": 0.5,
            "samples": samples,
            "error_rate": 0.0 if samples == 0 else errors / samples,
            "p50_latency_ms": int(avg_latency_ms),
            "tool_success_rate": (
                tool_successes / tool_turns if tool_turns else 0.0
            ),
            "is_cold_start": True,
        }

    error_rate = errors / samples
    availability = 1.0 - error_rate
    # Linear decay from full credit at <=2s to zero at >=20s.
    if avg_latency_ms <= 2000:
        speed = 1.0
    elif avg_latency_ms >= 20000:
        speed = 0.0
    else:
        speed = 1.0 - (avg_latency_ms - 2000) / 18000.0
    tool_success_rate = (
        tool_successes / tool_turns if tool_turns else 1.0
    )
    score = 0.60 * availability + 0.25 * speed + 0.15 * tool_success_rate
    return {
        "score": round(score, 4),
        "samples": samples,
        "error_rate": round(error_rate, 4),
        "p50_latency_ms": int(avg_latency_ms),
        "tool_success_rate": round(tool_success_rate, 4),
        "is_cold_start": False,
    }

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
