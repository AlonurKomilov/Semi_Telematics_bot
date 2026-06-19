"""Nightly Driver Scorecard snapshot job.

Persists yesterday's composite scores into ``daily_scorecard_snapshots``
for every account.  Two purposes:

  1. **Vendor-independence** — if Samsara is unreachable the next day,
     the dashboard still has a recent score to display.
  2. **History / trends** — enables the score-over-time chart in the UI
     without re-querying Samsara for every page load.

writes one row per *driver* AND one per *vehicle*
each night, so the new vehicle-primary scorecard view has trend data
from day one.  ``daily_scorecard_snapshots`` keys on
``(account_id, snapshot_date, subject_type, subject_id)`` so the two
subject families coexist without collision.

Runs once per day at 02:30 UTC via APScheduler (registered in
``interfaces/bot/scheduler.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from capabilities.scorecards.service import evaluate_subjects
from infra.services import get_platform_db, get_tenant_db
from capabilities.alerting.registry import register_alert_source


logger = logging.getLogger(__name__)


# snapshot both subjects. Order matters \u2014 driver first so a
# tenant looking at a fresh "yesterday" scorecard while the job is still
# in flight sees driver data first (legacy default), then vehicle.
SUBJECTS_TO_SNAPSHOT: tuple[str, ...] = ("driver", "vehicle")


def _breakdown_payload(card: dict) -> dict:
    """Shape what we persist into ``breakdown_json``.

    Includes the Option-C ``pillars`` block so the
    ``/safety/scorecards/history?pillar=...`` endpoint can rebuild
    per-pillar history without a second Samsara round-trip.  Without
    this, pillar history filters silently return empty arrays for
    snapshots taken pre-Option-C \u2014 a regression we now fix.
    """
    return {
        "base":          card.get("base", 0),
        "bonus_total":   card.get("bonus_total", 0),
        "penalty_total": card.get("penalty_total", 0),
        "bonuses":       card.get("bonuses", []),
        "penalties":     card.get("penalties", []),
        "company":       card.get("company", ""),
        # Option-C pillar block \u2014 required for pillar history filter.
        "pillars":       card.get("pillars", {}),
        # exposure block \u2014 lets the dashboard show
        # "miles in window" on hover without extra fetches.
        "exposure":      card.get("exposure", {}),
        "insufficient_data": bool(card.get("insufficient_data", False)),
    }


async def take_daily_scorecard_snapshots(_app=None) -> None:
    """For every active account, snapshot yesterday's scorecards \u2014
    once per subject (driver + vehicle)."""
    snapshot_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    accounts = await get_platform_db().list_accounts(active_only=True)
    totals: dict[str, int] = {s: 0 for s in SUBJECTS_TO_SNAPSHOT}
    failed: list[tuple[int, str]] = []

    from capabilities.localization.tz import is_local_hour as _is_local_hour
    _TARGET_HOUR = 2  # local 02:00 — pre-shift, after yesterday's data settled
    for acc in accounts:
        # Hourly tick + per-account gate replaces the old fleet-wide
        # 02:30 UTC cron, so an Eastern fleet snapshots at 02:00 ET
        # (07:00 UTC) and a Pacific fleet at 02:00 PT (10:00 UTC).
        if not await _is_local_hour(acc.id, _TARGET_HOUR):
            continue
        tenant = await get_tenant_db(acc.id)
        if tenant is None:
            continue
        # Stamp ``app.account_id`` for RLS-enforced mode — every mixin
        # query plus the explicit ``tenant._db.execute`` calls below
        # rely on this GUC to find their rows.
        async with tenant.with_account(acc.id):
            for subject in SUBJECTS_TO_SNAPSHOT:
                try:
                    cards = await evaluate_subjects(
                        acc.id, subject=subject, days=7,
                        write_evidence=True, evidence_date=snapshot_date,
                    )
                except Exception:
                    logger.exception(
                        "scorecard snapshot: evaluate_subjects(%s) failed for account %d",
                        subject, acc.id,
                    )
                    failed.append((acc.id, subject))
                    continue

                for card in cards:
                    try:
                        sid = str(card.get("subject_id") or card.get("driver_id")
                                  or card.get("driver_name"))
                        sname = str(card.get("subject_name") or card.get("driver_name") or sid)
                        await tenant.save_scorecard_snapshot(
                            acc.id,
                            snapshot_date=snapshot_date,
                            subject_type=subject,
                            subject_id=sid,
                            subject_name=sname,
                            total_score=int(card["score"]),
                            window_days=7,
                            breakdown=_breakdown_payload(card),
                            source="nightly",
                        )
                        totals[subject] += 1
                    except Exception:
                        logger.exception(
                            "scorecard snapshot: save failed acct=%d subject=%s id=%s",
                            acc.id, subject, card.get("subject_id") or card.get("driver_id"),
                        )

    logger.info(
        "scorecard snapshots written: drivers=%d vehicles=%d across %d accounts "
        "(date=%s, failed=%s)",
        totals.get("driver", 0), totals.get("vehicle", 0),
        len(accounts), snapshot_date, failed or "none",
    )

    # (Pruning score_events to its 90-day window now runs through the
    # Retention hub's nightly tenant pass — scorecards.score_history.  See
    # capabilities/retention + the ``data_retention`` scheduler job.)


# Default thresholds for score-drop alerting.
# Accounts can override these via account_settings (scorecard_alert_drop_threshold
# and scorecard_alert_floor_threshold).
DEFAULT_DROP_THRESHOLD  = 10   # alert if score falls by ≥ N points week-over-week
DEFAULT_FLOOR_THRESHOLD = 60   # alert if score is at or below this value


@register_alert_source("scorecard_drop_alerts", trigger="cron", minute=0)
async def check_scorecard_drop_alerts(_app=None) -> None:
    """After nightly snapshots are written, send alerts for significant score drops.

    For each account, compares yesterday's snapshot to the snapshot from 7 days
    prior.  Fires a Telegram notification to ``alert_events`` subscribers when:
      - A subject's score dropped by ≥ DROP_THRESHOLD points, OR
      - A subject's score is at or below FLOOR_THRESHOLD.

    Runs immediately after ``take_daily_scorecard_snapshots`` in the nightly job.
    """
    from infra.platform import get_app_for_account

    yesterday  = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    prior_week = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

    accounts = await get_platform_db().list_accounts(active_only=True)

    from capabilities.localization.tz import is_local_hour as _is_local_hour
    _TARGET_HOUR = 3  # local 03:00 — runs the hour after snapshots
    for acc in accounts:
        # Same hourly-tick-with-local-gate pattern as the snapshot job.
        if not await _is_local_hour(acc.id, _TARGET_HOUR):
            continue
        tenant = await get_tenant_db(acc.id)
        if tenant is None:
            continue

        # Stamp ``app.account_id`` once for the entire per-account
        # block so the raw ``tenant._db.execute`` reads below land
        # under the right RLS policy.
        async with tenant.with_account(acc.id):
            # Load per-account thresholds (fall back to defaults)
            try:
                raw_drop  = await tenant.get_account_setting(acc.id, "scorecard_alert_drop_threshold", "")
                raw_floor = await tenant.get_account_setting(acc.id, "scorecard_alert_floor_threshold", "")
                drop_threshold  = int(raw_drop)  if raw_drop.isdigit()  else DEFAULT_DROP_THRESHOLD
                floor_threshold = int(raw_floor) if raw_floor.isdigit() else DEFAULT_FLOOR_THRESHOLD
            except Exception:
                drop_threshold  = DEFAULT_DROP_THRESHOLD
                floor_threshold = DEFAULT_FLOOR_THRESHOLD

            # Gather subscribers
            try:
                subscribers = await tenant.get_typed_alert_subscribers(acc.id, "events")
            except Exception:
                continue
            if not subscribers:
                continue

            bot_app = get_app_for_account(acc.id)
            if not bot_app:
                continue

            # Check snapshots for both subject types
            for subject in SUBJECTS_TO_SNAPSHOT:
                try:
                    # Both SELECTs in one transaction so a concurrent snapshot
                    # writer can't insert/delete between the two reads and
                    # invalidate the today-vs-prior comparison.  Read-only
                    # transaction is enough; no UPDATE here.
                    async with tenant.transaction():
                        cur = await tenant._db.execute(
                            """SELECT subject_id, subject_name, total_score
                               FROM daily_scorecard_snapshots
                               WHERE account_id = ? AND subject_type = ? AND snapshot_date = ?""",
                            (acc.id, subject, yesterday),
                        )
                        today_rows = {r["subject_id"]: r for r in await cur.fetchall()}

                        cur = await tenant._db.execute(
                            """SELECT subject_id, total_score
                               FROM daily_scorecard_snapshots
                               WHERE account_id = ? AND subject_type = ? AND snapshot_date = ?""",
                            (acc.id, subject, prior_week),
                        )
                        prior_rows = {r["subject_id"]: r["total_score"] for r in await cur.fetchall()}
                except Exception:
                    logger.exception("scorecard_drop_alerts: DB read failed acct=%d subject=%s", acc.id, subject)
                    continue

                at_risk: list[tuple[str, str, int, int | None]] = []  # (sid, name, score, prior)
                for sid, row in today_rows.items():
                    score_now = row["total_score"]
                    score_prior = prior_rows.get(sid)
                    drop = (score_prior - score_now) if score_prior is not None else None
                    if score_now <= floor_threshold or (drop is not None and drop >= drop_threshold):
                        at_risk.append((sid, row["subject_name"], score_now, score_prior))

                if not at_risk:
                    continue

                # Build alert message
                subject_label = "truck" if subject == "vehicle" else "driver"
                lines = [f"<b>⚠️ Scorecard Alert — {len(at_risk)} {subject_label}(s) need attention</b>"]
                for sid, name, score_now, score_prior in sorted(at_risk, key=lambda x: x[2]):
                    if score_prior is not None:
                        delta = score_prior - score_now
                        lines.append(f"• {name}: <b>{score_now}</b> (▼{delta} from {score_prior})")
                    else:
                        lines.append(f"• {name}: <b>{score_now}</b> (floor alert)")
                lines.append(f"\n<i>Threshold: floor ≤{floor_threshold} or drop ≥{drop_threshold} pts</i>")
                text = "\n".join(lines)

                # Forum routing first.  When the account has a Scorecards
                # topic configured the digest lands there once; otherwise
                # we fall through to the legacy per-subscriber DM fanout.
                from capabilities.alerting.pipeline import post_alert_to_topic
                posted = await post_alert_to_topic(
                    bot_app, account_id=acc.id,
                    alert_type="scorecard", text=text,
                    severity="warning",
                )
                if posted:
                    continue

                for sub in subscribers:
                    if not sub.alerts_on:
                        continue
                    try:
                        await bot_app.bot.send_message(
                            chat_id=sub.telegram_id,
                            text=text,
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.warning(
                            "scorecard_drop_alerts: send failed acct=%d user=%d: %s",
                            acc.id, sub.telegram_id, e,
                        )
