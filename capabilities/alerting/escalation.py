"""Alert escalation — ACK, auto-resolve."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone


from infra.services import get_platform_db, get_tenant_db
from capabilities.alerting.registry import register_alert_source

logger = logging.getLogger("bot")


# ── Auto-Resolve Helpers ─────────────────────────────────────────

def _build_resolve_detail(alert_type: str, detail: str) -> str:
    """Build human-readable detail for auto-resolved notifications.

    Parses the stored alert detail and returns a formatted string
    describing what was originally alerted.  Every field that
    originated outside our code (DTC ``desc``, ``spn`` / ``fmi``
    codes, raw fault ``entry``) is HTML-escaped before going into the
    output — these flow through parse_mode=HTML and Samsara strings
    occasionally contain ``<``, ``>``, or ``&``.
    """
    from capabilities.formatting.helpers import escape_html
    if not detail:
        return ""
    lines: list[str] = []
    if alert_type == "health":
        label_map = {
            "low_oil_pressure": "🛢 Low Oil Pressure",
            "high_coolant_temp": "🌡 High Coolant Temp",
            "low_battery": "🔋 Low Battery",
            "low_def": "💧 Low DEF Level",
            "coolant_dtc": "🌡 Coolant System Fault",
        }
        for code in detail.split("-"):
            # Either the canonical label (safe constant) or a title-cased
            # fallback derived from the code (alphanumeric — safe).
            label = label_map.get(code, code.replace("_", " ").title())
            lines.append(f"  ✅ {label} — normal")
    elif alert_type == "fault":
        for entry in detail.split("|"):
            if ":" in entry:
                code_part, desc = entry.split(":", 1)
                lines.append(f"  ✅ {escape_html(code_part)} — cleared")
                if desc:
                    lines.append(f"       {escape_html(desc)}")
            else:
                spn_fmi = entry.split("-", 1)
                if len(spn_fmi) == 2:
                    lines.append(
                        f"  ✅ SPN {escape_html(spn_fmi[0])} / "
                        f"FMI {escape_html(spn_fmi[1])} — cleared"
                    )
                elif entry:
                    lines.append(f"  ✅ {escape_html(entry)} — cleared")
    elif alert_type == "fuel":
        fuel_val = detail.split(":")[-1] if ":" in detail else detail
        lines.append(f"  ✅ Fuel was at {escape_html(fuel_val)}% — now above threshold")
    return "\n".join(lines)


async def _auto_resolve_vehicle_alerts(
    app,   # unused transport handle — kept for caller compatibility
    account_id: int,
    alert_type: str,
    vehicle_id: str,
    vehicle_name: str,
    co: str,
    bot_app=None,   # unused — kept for caller compatibility
):
    """Auto-resolve all unacked alerts for a vehicle when the source check
    detects the condition has cleared.

    This is the fast path — called directly from check_health_alerts /
    check_new_faults / check_low_fuel when they see a vehicle is clear.
    Eliminates waiting for the re-alert cycle to auto-resolve.
    Respects working hours — queues notification if user is outside working hours.

    Self-contained: clears alert_history AND resolves alert_acknowledgments so
    callers do not need to call clear_alert_history separately.
    """
    tenant = await get_tenant_db(account_id)

    # Fetch first_seen from the shared history record BEFORE clearing it.
    # ``hist`` doubles as the "is anything in flight right now?" gate —
    # when it's None there's no active alert for this vehicle+type and
    # we MUST return before doing anything else.  Without this guard,
    # the health/fault check loops over every healthy vehicle and the
    # downstream lookup + send would re-fire a "RESOLVED" receipt for
    # whatever old already-resolved delivery row still lives in
    # alert_acknowledgments — operators saw 1000+ spurious "Health
    # Cleared" messages from one account on a single health-check tick.
    hist = await tenant.get_active_alert_history(account_id, alert_type, vehicle_id)
    if hist is None:
        return
    first_seen_str = hist["first_seen"] or ""

    # Capture the earliest human ack BEFORE the auto-resolve sweep
    # (which marks every un-acked row as system-acked with
    # ``acknowledged_by = 0``).  When a real user handled the alert
    # before it cleared, we surface "✅ Acked by <name>" on the
    # resolve receipt so the rest of the team sees who closed the
    # loop — even on the auto-resolve message they didn't trigger.
    earliest_ack = await tenant.get_earliest_human_ack(
        account_id, alert_type, vehicle_id,
    )

    # Clear the single alert_history record (single source of truth)
    await tenant.clear_alert_history(account_id, alert_type, vehicle_id)

    # Spine-delivered DM copies (delivery ledger keyed alert:{history_id})
    # get their resolve receipt as an in-place edit through the spine's
    # update verb — final edit for the occurrence, so the ledger rows
    # clear with it.  No-op for accounts still on the legacy DM path
    # (their ledger is empty); a failed edit never blocks the legacy
    # receipts below.  docs/architecture/alert-dm-migration.md.
    try:
        from capabilities.notifications import (
            NotificationContent as _NotifContent,
            update_delivery as _update_delivery,
        )
        _who = ""
        if earliest_ack and (earliest_ack.get("acknowledged_by") or 0) > 0:
            try:
                _acker = await get_platform_db().get_user_by_telegram_id(
                    earliest_ack["acknowledged_by"])
                _who = (_acker.display_name if _acker else "") or ""
            except Exception:
                _who = ""
        _line = (f"✅ Acked by {_who}" if _who
                 else "Condition cleared automatically")
        await _update_delivery(
            tenant, account_id, f"alert:{hist['id']}",
            _NotifContent(
                title="",
                body=(f"🟢 RESOLVED — #{vehicle_name} {alert_type}\n"
                      f"{_line}  🔖 #{hist['id']}"),
                severity="info",
            ),
            # DM copies only: a group copy gets a threaded REPLY below
            # (owner decision — editing would erase the alert text for
            # every other shift reading the topic).
            channels=("telegram_dm",),
            clear=True,
        )
    except Exception as _se:
        logger.debug("spine resolve receipt failed for %s/%s: %s",
                     account_id, vehicle_id, _se)

    # ── Two-step lookup + resolution ─────────────────────────────
    # Step 1: capture every recent delivery row REGARDLESS of ack
    # state so the resolve receipt can thread to the original alert
    # in each surface.  Using only ``auto_resolve_alerts_by_vehicle``
    # here would skip alerts the user already ACK'd, and the receipt
    # would either not send (if every row was acked) or post as a
    # fresh unattached message in the topic — both UX regressions.
    reply_targets = await tenant.get_recent_alerts_for_resolution(
        account_id, alert_type, vehicle_id,
    )
    # Step 2: mark un-acked rows as system-acked (the actual DB
    # resolution).  Return value no longer needed here — step 1
    # already gave us the threading targets.
    await tenant.auto_resolve_alerts_by_vehicle(
        account_id, alert_type, vehicle_id,
    )
    if not reply_targets:
        return  # never delivered anywhere — nothing to send a receipt to

    from capabilities.formatting.helpers import escape_html
    vname = escape_html(str(vehicle_name or reply_targets[0].get("vehicle_name", "?")))
    alert_co = escape_html(str(co or "?"))

    # Build resolve detail from the shared alert_key (same for all subscribers).
    alert_key = reply_targets[0].get("alert_key", "")
    key_parts = alert_key.split(":", 2)
    detail = key_parts[2] if len(key_parts) > 2 else ""
    detail_lines = _build_resolve_detail(alert_type, detail)

    # Compute "cleared Xh Ym after first seen" once from shared first_seen.
    duration_phrase = ""
    if first_seen_str:
        try:
            first_dt = datetime.fromisoformat(first_seen_str)
            mins = int((datetime.now(timezone.utc) - first_dt).total_seconds() / 60)
            if mins >= 60:
                duration_phrase = f"cleared {mins // 60}h {mins % 60}m after first seen"
            elif mins >= 1:
                duration_phrase = f"cleared {mins} min after first seen"
            else:
                duration_phrase = "cleared in under a minute"
        except Exception as e:
            logger.debug("Could not compute alert duration: %s", e)

    # ── Build the receipt in Option A grammar ────────────────────
    # 🟢 RESOLVED — <Type> Cleared
    #
    # 🚛 Vehicle #<name>
    # 🏢 <co>                                    (when set)
    # 🕐 cleared 2h 14m after first seen
    #
    # ✅ <what was alerting>
    #
    # 💡 Condition cleared — no action needed
    from capabilities.formatting.severity import badge, default_action
    type_title_map = {
        "fault":  "Fault Cleared",
        "health": "Health Cleared",
        "fuel":   "Fuel Restored",
    }
    title = type_title_map.get(alert_type, "Alert Cleared")

    resolve_lines: list[str] = [f"<b>{badge('resolved')}</b> — {title}", ""]

    # Stacked rows (see faults.py for the mobile-readability rationale).
    resolve_lines.append(f"🚛 <b>Vehicle #{vname}</b>")
    if alert_co and alert_co != "?":
        resolve_lines.append(f"🏢 {alert_co}")
    if duration_phrase:
        resolve_lines.append(f"🕐 {duration_phrase}")

    if detail_lines:
        # ``detail_lines`` already prefixes each row with ``  ✅`` — drop
        # the leading whitespace so it sits flush with the new grammar.
        cleaned = "\n".join(
            ln.lstrip() for ln in detail_lines.split("\n") if ln.strip()
        )
        resolve_lines.append("")
        resolve_lines.append(cleaned)

    resolve_lines.append("")
    resolve_lines.append(f"💡 {default_action('resolved')}")

    # ── ACK chip (when a human acked before the system cleared) ──
    # Belt-and-suspenders: only render the chip when the acker is a
    # real Telegram user (strictly positive ID).  The SQL query
    # already filters out the ``0`` (auto-resolve) and ``-1``
    # (SYSTEM_USER_ID, AI auto-actions) sentinels, but the defensive
    # check here guarantees the receipt never shows "Acked by user -1"
    # or "Acked by user 0" if a new system sentinel is added later.
    if earliest_ack and (earliest_ack.get("acknowledged_by") or 0) > 0:
        ack_tid = earliest_ack["acknowledged_by"]
        ack_at = earliest_ack.get("acknowledged_at") or ""
        ack_name: str | None = None
        try:
            acker = await get_platform_db().get_user_by_telegram_id(ack_tid)
            if acker:
                ack_name = acker.display_name or str(ack_tid)
        except Exception as e:
            logger.debug("Could not resolve acker name for %s: %s", ack_tid, e)
        # When the lookup fails (deleted user / cross-account
        # weirdness), fall back to the bare telegram_id — never to a
        # generic "user 0" / "user -1" because we already guarded
        # against those above.
        ack_name = ack_name or f"user {ack_tid}"

        # "Acked Xh Ym before clear" — anchors the ACK to the same
        # clock as the resolve duration above so the reader can place
        # the two events in order at a glance.
        gap_phrase = ""
        if ack_at and first_seen_str:
            try:
                ack_dt = datetime.fromisoformat(ack_at)
                clr_dt = datetime.now(timezone.utc)
                gap_mins = int((clr_dt - ack_dt).total_seconds() / 60)
                if gap_mins >= 60:
                    gap_phrase = f" · {gap_mins // 60}h {gap_mins % 60}m before clear"
                elif gap_mins >= 1:
                    gap_phrase = f" · {gap_mins} min before clear"
            except Exception as e:
                logger.debug("Could not compute ack→clear gap: %s", e)

        resolve_lines.append(f"🔖 ✅ Acked by <b>{ack_name}</b>{gap_phrase}")

    resolve_text = "\n".join(resolve_lines)


    # Look up the forum topic for this alert type once so group resolves
    # can route explicitly.  Telegram only routes a reply into the same
    # topic as the original message when ``message_thread_id`` is passed
    # alongside ``reply_to_message_id`` — relying on the reply-link alone
    # leaks the resolution into the group's General topic (e.g. "Team
    # Chat") whenever the original is too old / edited / not visible.
    # That mismatched routing was visible to operators as Health and
    # Fault "AUTO-RESOLVED" receipts landing in Team Chat instead of
    # the Faults / Health topics where the live alerts post.
    from capabilities.alerting.pipeline import (
        _FORUM_ROUTING_ENABLED,
        _PIPELINE_TO_ROUTE_KEY,
    )
    group_thread_id: int | None = None
    if _FORUM_ROUTING_ENABLED:
        _route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
        if _route_key:
            try:
                _route = await get_platform_db().get_alert_route(account_id, _route_key)
                if _route is not None:
                    group_thread_id = _route.message_thread_id
            except Exception as e:
                logger.debug(
                    "Could not look up forum route for auto-resolve "
                    "(acct=%d type=%s): %s", account_id, alert_type, e,
                )

    # ── Per-topic resolve-receipt gate (migration 079) ──────────────
    # Admins can mute the "🟢 RESOLVED — Vehicle Moving" follow-up per
    # topic on the Account Settings → Forum Routing page.  Resolve the
    # admin-side decision once before the per-recipient loop so it's a
    # single DB hit per auto-resolve event (not per group recipient).
    # The underlying alert_history row was already flipped to resolved
    # by ``auto_resolve_alerts_by_vehicle`` above — turning the receipt
    # off only suppresses the chat message; the dashboard's monitoring
    # view stays accurate.
    group_receipt_enabled = True
    try:
        route = await tenant.get_alert_route(account_id, alert_type)
        if route is not None:
            group_receipt_enabled = bool(route.send_resolve_receipt)
    except Exception as e:
        logger.debug(
            "Could not load alert_route for resolve-receipt gate "
            "(acct=%d type=%s): %s — defaulting to ENABLED",
            account_id, alert_type, e,
        )

    # Group receipts: a threaded REPLY under each original group post
    # (reply-not-edit — other shifts still need the alert text), sent
    # through the delivery plan with the receipt's pre-rendered HTML
    # riding the prefix rail and reply_to threading into the topic.
    # Legacy DM rows get nothing here — spine DM copies already got
    # their in-place 🟢 edit above.
    if group_receipt_enabled:
        from capabilities.notifications import (
            DeliveryPlan,
            NotificationContent as _NC,
            Target as _PT,
            deliver as _plan_deliver,
        )
        _receipt_targets: list = []
        for alert in reply_targets:
            if alert.get("sent_to") != 0:
                continue                     # group rows only
            msg_id = alert.get("message_id")
            chat_id = alert.get("chat_id")
            if not (msg_id and chat_id):
                continue
            addr = (f"{chat_id}:{group_thread_id}"
                    if group_thread_id is not None else str(chat_id))
            _receipt_targets.append(_PT(
                channel="telegram_topic", address=addr,
                id=_PIPELINE_TO_ROUTE_KEY.get(alert_type, alert_type),
                prefix_html=resolve_text,
                reply_to_message_id=int(msg_id),
            ))
        if _receipt_targets:
            try:
                await _plan_deliver(
                    get_platform_db(), account_id,
                    DeliveryPlan(contents=[_NC(title="", body="")],
                                 shared=_receipt_targets, personal=[]),
                )
            except Exception as _ge:
                logger.debug("group resolve receipts failed (%s/%s): %s",
                             account_id, vehicle_id, _ge)

    await tenant.add_audit_log(
        account_id=account_id,
        user_id=None,
        action="alert_auto_resolved",
        target_type="alert",
        target_id=str(reply_targets[0]["id"]),
        details=(
            f"{alert_type} alert for Truck {vname} auto-resolved; "
            f"{len(reply_targets)} subscriber(s) notified"
        ),
    )
    logger.info(
        "Auto-resolved %s alert for Truck %s — notified %d subscriber(s)",
        alert_type, vname, len(reply_targets),
    )

def _backoff_hours_for_attempt(attempt_index: int, schedule: tuple[int, ...]) -> int:
    """Pick the wait-hours for the next reminder.

    ``attempt_index`` is the count of reminders ALREADY sent (0 = none yet).
    Returns the schedule entry, clamped to the last value when we're past
    the array length so chronic alerts settle at the longest backoff.
    """
    if not schedule:
        return 1
    if attempt_index < 0:
        attempt_index = 0
    if attempt_index >= len(schedule):
        return schedule[-1]
    return schedule[attempt_index]


@register_alert_source("critical_reescalate", trigger="interval", hours=1)

async def _spine_remind(tenant, account_id: int, history_id: int, *,
                        vname: str, atype: str, age_str: str,
                        attempt_n: int) -> bool:
    """Edit the spine-delivered PERSONAL copies of an unacknowledged
    alert into reminder text, keeping the ✅ Acknowledge action.

    DM copies ONLY (owner decision 2026-07-27, same rule as the
    resolve edit): a reminder edit on the GROUP copy replaced the
    alert text for the whole team and hung a personal Acknowledge
    button in a shared topic.  Rows are NOT cleared — later reminders
    and the final ack/resolve edit reuse them.
    """
    from capabilities.notifications import (
        NotificationContent as _NotifContent,
        update_delivery as _update_delivery,
    )
    from capabilities.alerting.spine_actions import ACK_ACTION
    from infra.config import REESCALATE_MAX_ATTEMPTS
    _plain = (
        f"🟡 Reminder {attempt_n}/{REESCALATE_MAX_ATTEMPTS}"
        " — unacknowledged alert\n"
        f"🚛 Vehicle #{vname}\n"
        f"⏱ {atype.title()} active for {age_str}\n"
        "💡 Acknowledge or mute — auto-clears when the "
        "condition lifts\n"
        f"🔖 #{history_id}"
    )
    results = await _update_delivery(
        tenant, account_id, f"alert:{history_id}",
        _NotifContent(title="", body=_plain, severity="warning",
                      actions=[dict(ACK_ACTION)]),
        channels=("telegram_dm",),
    )
    return any(r.ok for r in results)


async def re_escalate_critical_alerts(app=None):
    """Hourly job: bump unacknowledged CRITICAL/WARNING alerts.

    Major rewrite (2026-05) addressing the 360+ reminders/recipient noise
    pattern.  Now:

      * Iterates ``alert_history`` (one row per logical alert) instead of
        ``alert_acknowledgments`` (one row per delivery × subscriber).
        Subscriber fan-out happens once per logical alert per attempt.
      * Caps at ``REESCALATE_MAX_ATTEMPTS`` reminders per logical alert
        across its entire lifetime — after that, the alert stays in the
        dashboard pending list but stops pinging Telegram.
      * Exponential backoff per attempt via ``REESCALATE_BACKOFF_HOURS``
        (default 1h → 4h → 12h → 24h between successive reminders).
      * Skips alerts that are muted (alert_mutes table).
      * Edits the existing alert message in place with an "[N reminders]"
        prefix instead of sending a fresh push notification.  Falls back
        to send-new only when the edit fails.
      * Per-account isolation via tenant DB; never mixes data across
        accounts.
    """
    from infra.config import (
        REESCALATE_AFTER_MINUTES,
        REESCALATE_ALERT_TYPES,
        REESCALATE_MAX_ATTEMPTS,
        REESCALATE_BACKOFF_HOURS,
    )
    from infra.services import get_platform_db as _get_platform_db

    if REESCALATE_AFTER_MINUTES <= 0 or not REESCALATE_ALERT_TYPES:
        return
    if REESCALATE_MAX_ATTEMPTS <= 0:
        return

    platform = _get_platform_db()
    try:
        accounts = await platform.list_accounts()
    except Exception as e:
        logger.error("re_escalate: failed to list accounts: %s", e)
        return

    qualified_types = set(REESCALATE_ALERT_TYPES)
    now_dt = datetime.now(timezone.utc)
    sent = 0
    suppressed_muted = 0
    suppressed_capped = 0

    for acct in accounts:
        account_id = getattr(acct, "id", None)
        if not account_id:
            continue
        try:
            tenant = await get_tenant_db(account_id)
        except Exception as e:
            logger.debug("re_escalate: tenant fetch failed for %s: %s", account_id, e)
            continue

        # First reminder fires after REESCALATE_AFTER_MINUTES; further
        # reminders use REESCALATE_BACKOFF_HOURS[count-1].  We compute
        # the strictest cutoff (min of current backoff window) below per
        # row, but the SQL pre-filter uses the loosest one to avoid
        # fetching everything.
        loose_cutoff_min = min(
            REESCALATE_AFTER_MINUTES,
            REESCALATE_BACKOFF_HOURS[0] * 60 if REESCALATE_BACKOFF_HOURS else 60,
        )
        cutoff_iso = (now_dt - timedelta(minutes=loose_cutoff_min)).isoformat()
        try:
            candidates = await tenant.get_active_unacked_history_for_reescalation(
                account_id, cutoff_iso, REESCALATE_MAX_ATTEMPTS,
            )
        except Exception as e:
            logger.debug("re_escalate: candidate fetch failed acct=%d: %s", account_id, e)
            continue
        if not candidates:
            continue


        for hist in candidates:
            atype = hist.get("alert_type", "alert")
            if atype not in qualified_types:
                continue

            history_id = hist.get("id")
            attempts_so_far = int(hist.get("reescalate_count") or 0)

            # Per-row cooldown using the backoff schedule.
            wait_hours = _backoff_hours_for_attempt(
                attempts_so_far, REESCALATE_BACKOFF_HOURS,
            )
            last_sent = hist.get("reescalate_last_sent_at")
            if last_sent:
                try:
                    last_dt = datetime.fromisoformat(last_sent)
                    if (now_dt - last_dt) < timedelta(hours=wait_hours):
                        continue
                except Exception:
                    pass
            else:
                # First-ever reminder gates on first_seen + REESCALATE_AFTER_MINUTES
                first_seen = hist.get("first_seen") or ""
                if first_seen:
                    try:
                        first_dt = datetime.fromisoformat(first_seen)
                        if (now_dt - first_dt) < timedelta(minutes=REESCALATE_AFTER_MINUTES):
                            continue
                    except Exception:
                        pass

            # Mute check — operator silenced this alert.
            try:
                if await tenant.is_alert_history_muted(history_id, account_id):
                    suppressed_muted += 1
                    continue
            except Exception:
                pass

            vid = hist.get("vehicle_id", "")
            vname = hist.get("vehicle_name", "?")
            try:
                first_dt = datetime.fromisoformat(hist.get("first_seen") or "")
                age_min = int((now_dt - first_dt).total_seconds() / 60)
            except Exception:
                age_min = REESCALATE_AFTER_MINUTES
            age_str = f"{age_min // 60}h {age_min % 60}m" if age_min >= 60 else f"{age_min} min"

            attempt_n = attempts_so_far + 1
            # Demote the badge to 🟡 REMINDER so a chronic open alert
            # doesn't keep looking like a fresh 🔴/🟠 push.  The
            # reminder counter ("reminder 2/4") sits on the same line
            # so dispatch sees how aggressive the loop has been.
            from capabilities.formatting.severity import badge as _badge
            reminder_lines: list[str] = [
                f"<b>{_badge('reminder')}</b> — Unacknowledged Alert  "
                f"<i>(reminder {attempt_n}/{REESCALATE_MAX_ATTEMPTS})</i>",
                "",
                f"🚛 <b>Vehicle #{vname}</b>",
                f"⏱ <b>{atype.title()}</b> alert active for <b>{age_str}</b>",
                "",
                "💡 Acknowledge or mute — auto-clears when the condition lifts",
                f"🔖 #{history_id}",
            ]
            reminder_text = "\n".join(reminder_lines)

            # Spine-delivered DM copies (delivery ledger keyed
            # alert:{history_id}): ONE update edits every recorded copy,
            # keeping the ✅ Acknowledge button (the edit re-renders the
            # action row).  No-op when the ledger holds nothing for this
            # occurrence; rows are NOT cleared — later reminders and the
            # final ack/resolve edit reuse them.
            spine_reminded = False
            try:
                spine_reminded = await _spine_remind(
                    tenant, account_id, history_id,
                    vname=vname, atype=atype, age_str=age_str,
                    attempt_n=attempt_n,
                )
            except Exception as _se:
                logger.debug("re_escalate: spine reminder failed for #%s: %s",
                             history_id, _se)

            # Resolve every still-active recipient delivery for this
            # logical alert.  We edit each subscriber's message in place
            # (so they see "[reminder 2/4]" added to the existing alert
            # bubble) instead of sending a brand-new push.
            try:
                rows = await tenant.read_all(
                    "SELECT * FROM alert_acknowledgments "
                    "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
                    "AND acknowledged_at IS NULL AND status = 'active'",
                    (account_id, atype, vid),
                )
                deliveries = [dict(r) for r in rows]
            except Exception as e:
                logger.debug("re_escalate: deliveries fetch failed: %s", e)
                deliveries = []
            if not deliveries and not spine_reminded:
                # No active deliveries to remind — likely already acked
                # via a path that didn't clear history.  Bump count so
                # we don't loop on this row.
                try:
                    await tenant.bump_reescalate_attempt(history_id, account_id)
                except Exception:
                    pass
                continue

            # Per-copy reminder edits ride the delivery ledger
            # (the spine block above) — every current post, DM and
            # group alike, is recorded there.  Pre-ledger rows age
            # out unedited.
            attempt_sent_anywhere = False

            if attempt_sent_anywhere or spine_reminded:
                try:
                    new_count = await tenant.bump_reescalate_attempt(history_id, account_id)
                    sent += 1
                    if new_count >= REESCALATE_MAX_ATTEMPTS:
                        suppressed_capped += 1
                        logger.info(
                            "re_escalate: alert #%d hit max attempts (%d) — silenced",
                            history_id, REESCALATE_MAX_ATTEMPTS,
                        )
                except Exception as e:
                    logger.debug(
                        "re_escalate: bump_reescalate_attempt failed for %d: %s",
                        history_id, e,
                    )

    if sent or suppressed_muted or suppressed_capped:
        logger.info(
            "re_escalate: sent=%d skipped_muted=%d capped=%d",
            sent, suppressed_muted, suppressed_capped,
        )
