"""Fault alert checks — detect new DTC codes and send alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from cachetools import LRUCache
from telegram.ext import Application

import infra.cache as rcache
from adapters.samsara.client import populate_company_display
from capabilities.alerting.ai_maintenance import (
    _get_ai_diagnosis_note, _notify_api_errors, auto_create_maintenance_from_faults,
)
from capabilities.alerting.escalation import _auto_resolve_vehicle_alerts
from capabilities.alerting.pipeline import (
    AlertSeverity, COOLANT_SPNS,
    send_alert, is_vehicle_suppressed,
)
from capabilities.formatting import format_fault_alert
from capabilities.telemetry.service import get_vehicles_with_faults as _svc_get_faults
from infra.bot_registry import get_app_for_account
from infra.config import FAULT_ALERT_COOLDOWN_HOURS
from infra.context import set_tenant_display
from infra.isolation import run_account_job
from infra.services import get_client, get_platform_db, get_tenant_db

logger = logging.getLogger("bot")

# Fault-code dedup cache — canonical home (used only here).
_known_faults: dict = LRUCache(maxsize=10_000)  # "acct:ORG:vid" → set(codes)

# J1939 SPNs related to coolant system — re-exported from pipeline for
# backward-compat but canonical definition lives there.


# ── Fault-alert dedup helpers (Redis → in-memory) ────────────────

async def _get_known_faults(account_id: int, vid: str) -> set[str]:
    """Get previously known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        return await rcache.smembers(f"t:{account_id}:faults:{vid}")
    return _known_faults.get(f"{account_id}:{vid}", set())


async def _set_known_faults(account_id: int, vid: str, codes: set[str]):
    """Store known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        await rcache.sset(f"t:{account_id}:faults:{vid}", codes, ttl=86400)
    else:
        _known_faults[f"{account_id}:{vid}"] = codes


# ── Phase 3 — DB dedup shadow (parallel run) ─────────────────────
#
# The plan replaces Redis ``_known_faults`` with the DB query
# ``vehicle_fault_detail.cleared_at IS NULL``.  We run both in
# parallel for one release: Redis remains authoritative for the
# alerting decision (so behaviour is bit-for-bit identical), and the
# DB-backed view is sampled for divergence so we can flip authority
# in the next release with confidence.
#
# Divergence is logged at INFO once per (account, vehicle) per cycle
# so a noisy fleet doesn't flood the log.

async def _shadow_compare_dedup(
    account_id: int, vehicle_id: str, redis_codes: set[str],
) -> None:
    """Compare Redis dedup against ``vehicle_fault_detail`` and log diff.

    Pure observation — never raises, never affects alerting.  The DB
    set comes from the *active* (``cleared_at IS NULL``) DTC ids; the
    Redis set is the ``spnId-fmiId`` pairs the alert-loop tracks.  Both
    keys are derived from the same Samsara payload so they should
    match for any vehicle the warehouse ingest has touched.
    """
    try:
        tenant = await get_tenant_db(account_id)
        if tenant is None:
            return
        db_ids = await tenant.get_active_fault_dtc_ids(
            account_id, vehicle_id=vehicle_id,
        )
    except Exception:
        # Shadow mode must never break the alert loop.
        return

    # Redis stores "spn-fmi" pairs; the warehouse stores either the
    # Samsara DTC id or our fallback "spn:N-fmi:N".  Normalise both
    # sides to the spn/fmi tuple form before comparing.
    def _norm(s: str) -> str:
        s = s.strip()
        if s.startswith("spn:") and "-fmi:" in s:
            spn = s.split("spn:", 1)[1].split("-", 1)[0]
            fmi = s.split("-fmi:", 1)[1]
            return f"{spn}-{fmi}"
        return s

    norm_db = {_norm(x) for x in db_ids}
    norm_redis = {_norm(x) for x in redis_codes}
    if norm_db != norm_redis:
        only_db = norm_db - norm_redis
        only_redis = norm_redis - norm_db
        logger.info(
            "fault dedup shadow diff acct=%d vid=%s only_db=%s only_redis=%s",
            account_id, vehicle_id,
            sorted(only_db)[:5], sorted(only_redis)[:5],
        )


# ── Fault alert cooldown helpers ─────────────────────────────────

# LRUCache caps in-memory growth when Redis is unavailable.
# Faults LRU is consistent with _known_faults above.
_fault_last_sent: dict = LRUCache(maxsize=10_000)


async def _get_fault_last_sent(account_id: int, vid: str) -> float:
    """Get timestamp of last fault alert sent for this vehicle."""
    if rcache.is_available():
        val = await rcache.get(f"t:{account_id}:fault_ts:{vid}")
        return float(val) if val else 0.0
    return _fault_last_sent.get(f"{account_id}:{vid}", 0.0)


async def _set_fault_last_sent(account_id: int, vid: str):
    """Record that a fault alert was just sent for this vehicle."""
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(FAULT_ALERT_COOLDOWN_HOURS * 3600) + 300
    if rcache.is_available():
        await rcache.cache_set(f"t:{account_id}:fault_ts:{vid}", now, ttl=ttl)
    else:
        _fault_last_sent[f"{account_id}:{vid}"] = now


def _is_fault_on_cooldown(last_sent: float) -> bool:
    """Return True if a fault alert was sent too recently."""
    if last_sent == 0.0:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - last_sent) < (FAULT_ALERT_COOLDOWN_HOURS * 3600)


# ── API Health Notifications ────────────────────────────────────
# Implementation lives in capabilities.alerting.ai_maintenance so that
# faults / health / fuel can all share the same throttled notifier.


# ═══════════════════════════════════════════════════════════════════
#  Main fault-check scheduled job
# ═══════════════════════════════════════════════════════════════════

async def check_new_faults(app: Application):
    """Check all accounts with fault-alert subscribers for new faults.

    Classifies faults into CRITICAL (STOP/PROTECT/EMISSIONS lights or
    "Most Severe" FMI) and WARNING (normal faults). Uses the universal
    send_alert() pipeline for delivery.
    """
    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("faults")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            bot_app = get_app_for_account(account_id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping fault check", account_id)
                continue
            await run_account_job(
                _check_faults_account(bot_app, account_id, subs),
                account_id=account_id,
                job_name="fault_check",
            )

    except Exception as e:
        logger.error(f"Fault check error: {e}")


async def _check_faults_account(bot_app: Application, account_id: int, subs: list):
    """Process fault alerts for a single account."""
    samsara = await get_client(account_id)  # needed for last_skipped / ensure_org_ids
    faulted, _, _ = await _svc_get_faults(account_id)

    if samsara.last_skipped:
        await _notify_api_errors(bot_app, account_id, samsara.last_skipped)

    tenant = await get_tenant_db(account_id)
    acct_companies = await tenant.get_account_companies(account_id)
    populate_company_display(acct_companies)
    await samsara.ensure_org_ids()
    display = {co.code: co.display_name or co.code for co in acct_companies}
    set_tenant_display(display, samsara.org_ids)
    company_codes = [o.code for o in acct_companies]

    for v in faulted:
        try:
            co = v.get("_org", "?")
            vid = f"{account_id}:{co}:{v['id']}"

            if await is_vehicle_suppressed(account_id, v["name"]):
                continue

            current_codes = set()
            for dtc in v.get("_dtcs", []):
                key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                current_codes.add(key)

            previously_known = await _get_known_faults(account_id, vid)
            new_codes = current_codes - previously_known

            # Phase 3 shadow: compare Redis dedup vs DB warehouse view.
            # No behaviour change — just observability ahead of the
            # planned authority swap in the next release.
            await _shadow_compare_dedup(account_id, v["id"], previously_known)

            if new_codes:
                new_dtcs = [
                    dtc for dtc in v.get("_dtcs", [])
                    if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                ]
                if new_dtcs:
                    # ── Classify severity (pre-stamped by get_vehicles_with_faults) ──
                    _sev = v.get("_severity", "warning")
                    if _sev == "critical":
                        severity = AlertSeverity.CRITICAL
                    elif _sev == "info":
                        severity = AlertSeverity.INFO
                    else:
                        severity = AlertSeverity.WARNING

                    # Cooldown: skip WARNING/INFO faults if recently alerted
                    if severity == AlertSeverity.WARNING or severity == AlertSeverity.INFO:
                        last_sent = await _get_fault_last_sent(account_id, vid)
                        if _is_fault_on_cooldown(last_sent):
                            await _set_known_faults(account_id, vid, current_codes)
                            continue

                    # Check for coolant-related DTCs
                    has_coolant_dtc = any(
                        dtc.get("spnId") in COOLANT_SPNS
                        for dtc in new_dtcs
                    )

                    show_co = len(company_codes) > 1
                    lights = v.get("_lights", {})
                    alert_text = format_fault_alert(
                        v, new_dtcs, severity=severity, lights=lights, show_company=show_co,
                    )

                    if has_coolant_dtc:
                        alert_text += (
                            "\n\n  🌡 <b>Coolant system fault detected</b>"
                            "\n  Check coolant level and temp"
                        )

                    # Proactive AI — only if any subscriber enabled it
                    ai_note = ""
                    if any(getattr(s, 'ai_fault', False) for s in subs):
                        ai_note = await _get_ai_diagnosis_note(v, new_dtcs)

                    # Build fault detail with descriptions
                    fault_details = []
                    for dtc in new_dtcs:
                        spn = dtc.get('spnId', '?')
                        fmi = dtc.get('fmiId', '?')
                        desc = dtc.get('spnDescription', '')
                        fault_details.append(f"{spn}-{fmi}:{desc}")
                    fault_detail_str = "|".join(sorted(fault_details))

                    # ── Universal pipeline ───────────────
                    await send_alert(
                        bot_app,
                        account_id=account_id,
                        alert_type="fault",
                        severity=severity,
                        vehicle=v,
                        alert_text=alert_text,
                        subscribers=subs,
                        co=co,
                        ai_note=ai_note,
                        alert_key_detail=fault_detail_str,
                        bot_app=bot_app,
                    )

                    if severity == AlertSeverity.CRITICAL:
                        await auto_create_maintenance_from_faults(
                            account_id, v["name"], new_dtcs,
                        )

                    await _set_fault_last_sent(account_id, vid)

            await _set_known_faults(account_id, vid, current_codes)
        except Exception as e:
            logger.warning(f"Fault check for vehicle {v.get('name', '?')}: {e}")
            continue

    # ── Auto-resolve alerts for vehicles whose faults have cleared ─────────
    #
    # We cannot rely solely on _known_faults.keys() for stale-key detection
    # because _known_faults (in-memory LRU) is never populated when Redis is
    # available — all dedup state lives in Redis, so the local dict stays empty.
    # Instead we query alert_history (always in the DB) for all active fault
    # alerts belonging to this account and resolve any whose vehicle_id is not
    # in the set currently returned by Samsara.
    #
    # The in-memory dict scan is kept as a secondary pass to handle the
    # no-Redis (development/testing) case where the DB may not have a history
    # row yet for a just-seen vehicle.

    # Build a set of Samsara vehicle IDs (raw, not compound key) that are
    # currently faulted so we can match against alert_history.vehicle_id.
    current_faulted_raw_ids = {v["id"] for v in faulted}

    tenant = await get_tenant_db(account_id)

    # Primary: DB-based scan (works when Redis is available)
    try:
        active_fault_histories = await tenant.get_active_fault_history_for_account(account_id)
        for hist in active_fault_histories:
            v_id = hist["vehicle_id"]       # raw Samsara vehicle ID
            v_name = hist.get("vehicle_name", "")
            if v_id not in current_faulted_raw_ids:
                # Determine co from the compound _known_faults key if present,
                # otherwise fall back to empty string (auto-resolve still works).
                co_guess = ""
                compound = f"{account_id}:*:{v_id}"  # pattern — not used directly
                for k in list(_known_faults.keys()):
                    if k.endswith(f":{v_id}") and k.startswith(f"{account_id}:"):
                        parts = k.split(":", 2)
                        co_guess = parts[1] if len(parts) == 3 else ""
                        break
                await _auto_resolve_vehicle_alerts(
                    bot_app, account_id, "fault", v_id, v_name, co_guess,
                    bot_app=bot_app,
                )
                # Clear dedup so the fault is treated as new if it returns.
                # Pass v_id directly — _set_known_faults builds the compound
                # key internally as f"{account_id}:{vid}".
                await _set_known_faults(account_id, v_id, set())
    except Exception as e:
        logger.error("DB-based fault auto-resolve scan failed for account %d: %s", account_id, e)

    # Secondary: in-memory scan (no-Redis path — dict will be non-empty here)
    current_faulted_compound = {
        f"{account_id}:{v.get('_org', '?')}:{v['id']}"
        for v in faulted
    }
    stale_mem_keys = [
        k for k in list(_known_faults.keys())
        if k.startswith(f"{account_id}:")
        and k not in current_faulted_compound
    ]
    for k in stale_mem_keys:
        parts = k.split(":", 2)
        if len(parts) == 3:
            co = parts[1]
            vid = parts[2]   # raw Samsara vehicle ID — correct arg for _set_known_faults
            await _auto_resolve_vehicle_alerts(
                bot_app, account_id, "fault", vid, "", co,
                bot_app=bot_app,
            )
            # FIX (Bug 3): pass vid (raw vehicle ID), not k (full compound key).
            # _set_known_faults builds the internal key as f"{account_id}:{vid}"
            # so passing k would produce a double-prefixed key that never matches.
            await _set_known_faults(account_id, vid, set())


async def initialize_known_faults():
    """Pre-populate known faults for all accounts with alert subscribers."""
    try:
        subscribers = await get_platform_db().get_all_alert_subscribers()
        account_ids = set(s.account_id for s in subscribers)

        for account_id in account_ids:
            try:
                faulted, _, _ = await _svc_get_faults(account_id)
                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        codes.add(key)
                    await _set_known_faults(account_id, vid, codes)
            except Exception as e:
                logger.error(f"Init faults for account {account_id}: {e}")

        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")
