"""The sweep: read the fleet once, judge every trigger, DM the crossings.

ONE alert source serves every metric and every person — that is the point
of the catalog.  Adding "watch coolant" adds no job, no query and no
branch here; it adds a line to ``catalog.py``.

Three ideas carry the correctness:

**Crossings, not states.**  A trigger fires on the transition INTO breach,
never on the fact of being in breach.  Otherwise a truck sitting at 24%
for two days would DM its watcher every sweep.  The state lives in one
Redis flag per ``(trigger, vehicle)``, so two people watching fuel at 26%
and 15% keep entirely separate crossing histories — the trigger id is
what makes personal thresholds possible at all.

**Re-arm past a band, not back over the line.**  A reading hovering on the
threshold would otherwise clear and re-fire on every pass.  The band is
the metric's, in absolute units (``catalog.hysteresis``).

**Silence beats a guess.**  A vehicle is skipped — with no alert and no
state change — when its reading is stale, implausible, or measured with
the engine off for a metric that only means something running.  Three
different ways of not knowing, all of which used to be indistinguishable
from "fine".

First evaluation of a trigger SEEDS rather than fires: creating a trigger
at 60% when half the fleet is below it would otherwise open with fifty
DMs.  The seed marks who is already in breach, and the person hears about
the next truck to cross, which is what they asked for.
"""

from __future__ import annotations

import logging
from typing import Any

import infra.cache as rcache
from capabilities.alerting.registry import register_alert_source
from capabilities.alerting.triggers import catalog as cat
from capabilities.alerting.triggers.models import AlertTrigger
from capabilities.data_lifecycle.staleness import is_stale
from infra.isolation import run_account_job
from infra.services import get_platform_db, get_tenant_db

logger = logging.getLogger("bot")

#: The evaluator wakes on the fastest cadence any metric declares; each
#: metric is then judged only when its own period is due.  One job, one
#: query, no per-trigger scheduling.
SWEEP_MINUTES = 5

#: Crossing flags outlive a sweep but not a day: a stuck flag would
#: silence a real crossing forever, and a day is long enough that normal
#: hysteresis, not expiry, is what governs re-firing.
_FLAG_TTL_SECONDS = 24 * 60 * 60

#: Fallback when Redis is down.  Process-local, so a restart re-seeds
#: rather than re-alerts — the same posture the fuel checker takes.
_local_flags: dict[str, bool] = {}


def _flag_key(account_id: int, trigger_id: int, vehicle_id: str) -> str:
    return f"t:{account_id}:trig:{trigger_id}:{vehicle_id}"


async def _in_breach(key: str) -> bool:
    if rcache.is_available():
        return await rcache.exists(key)
    return _local_flags.get(key, False)


async def _set_breach(key: str, breached: bool) -> None:
    if rcache.is_available():
        if breached:
            await rcache.setex_flag(key, _FLAG_TTL_SECONDS)
        else:
            await rcache.delete(key)
        return
    if breached:
        _local_flags[key] = True
    else:
        _local_flags.pop(key, None)


def _due(metric: cat.Metric, tick: int) -> bool:
    """Is this metric's own period up on this sweep?

    ``tick`` counts sweeps, so a 15-minute metric is judged on every third
    5-minute sweep.  Deriving it from a counter rather than wall-clock
    keeps the decision testable without freezing time.
    """
    every = max(1, round(metric.check_every_minutes / SWEEP_MINUTES))
    return tick % every == 0


async def _latest_per_vehicle(tenant, account_id: int, columns: list[str]) -> list[dict]:
    """The newest row per vehicle from the MINUTE tier.

    Not the live tier: measured across this fleet the minute tier held the
    fresher reading for 102 vehicles out of 102, while live — upserted in
    place — carried rows nearly a month old.  And not through a service
    facade, several of which fall back to a provider API call when
    warehouse reads are off, which would put this sweep on the customer's
    Samsara quota.
    """
    cols = ", ".join(columns)
    cur = await tenant._db.execute(
        f"""SELECT DISTINCT ON (vehicle_id) {cols}
              FROM warehouse.vehicle_state_minute
             WHERE account_id = ?
             ORDER BY vehicle_id, captured_at DESC""",
        (account_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def _owner_scope(tenant, account_id: int, owner_user_id: int):
    """The trigger owner's vehicle scope, or None when unrestricted.

    A trigger is one person's, so it must see one person's fleet.  Without
    this a driver assigned to a single truck would be DM'd about all 102
    — vehicles they cannot open in the dashboard, which is a disclosure,
    not merely noise.  The board already scopes this way
    (``adapters/storage/alerts.py`` allowed_vehicle_names); the sweep now
    matches it, using the same identity ladder rather than a name
    comparison that once let 230 match 2303.
    """
    try:
        db = get_platform_db()
        user = await db.get_user_by_id(owner_user_id)
        role = str(getattr(getattr(user, "role", ""), "value", "")
                   or getattr(user, "role", "") or "")
        if role != "driver":
            return None
        trucks = await db.get_user_vehicle_nums(owner_user_id)
        if not trucks:
            # Legacy behaviour, kept deliberately and matching
            # deps.get_user_vehicle_scope: a driver with NO assignment at
            # all is unrestricted rather than blind.
            trucks = [getattr(user, "truck_num", "")] if getattr(user, "truck_num", "") else []
        if not trucks:
            return None
        from capabilities.permissions.vehicle_scope import build_vehicle_scope
        return await build_vehicle_scope(tenant, account_id, trucks)
    except Exception as e:
        # Fail CLOSED for a restricted owner is not possible without
        # knowing they are restricted — so an unresolvable scope means we
        # cannot prove the person may see these trucks, and the trigger
        # stays silent this sweep rather than guessing.
        logger.warning("trigger scope unresolved for user %s: %s",
                       owner_user_id, e)
        return _DENY_ALL


class _DenyAll:
    """Sentinel scope: allows nothing.  Used only when a scope could not
    be resolved, so a failure never widens what somebody sees."""

    def allows_row(self, row, **kw) -> bool:
        return False


_DENY_ALL = _DenyAll()


async def _names_for(tenant, account_id: int) -> dict[str, str]:
    """``vehicle_id → display name``, for the sentence a person reads.

    The MINUTE tier carries no ``vehicle_name`` — it is keyed by id — so
    the name comes from the live tier, which holds one row per vehicle
    and does carry it.  One extra query per sweep, and a miss simply
    leaves the id in the message rather than failing the sweep: a DM
    naming a truck by its provider id is poor, being silent is worse.
    """
    try:
        cur = await tenant._db.execute(
            "SELECT vehicle_id, vehicle_name FROM warehouse.vehicle_state_live "
            " WHERE account_id = ?",
            (account_id,),
        )
        return {str(r[0]): str(r[1] or "") for r in await cur.fetchall()}
    except Exception as e:
        logger.debug("trigger sweep: names unavailable acct=%s: %s", account_id, e)
        return {}


async def evaluate_account(
    account_id: int, triggers: list[AlertTrigger], tick: int,
) -> dict[str, int]:
    """Judge one account's triggers against one read of its fleet."""
    stats = {"fired": 0, "cleared": 0, "skipped": 0, "seeded": 0}
    due = [t for t in triggers if t.spec is not None and _due(t.spec, tick)]
    if not due:
        return stats

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return stats
    rows = await _latest_per_vehicle(
        tenant, account_id, cat.columns_needed({t.metric for t in due}))
    if not rows:
        return stats
    names = await _names_for(tenant, account_id)

    scopes: dict[int, Any] = {}
    for trig in due:
        metric = trig.spec
        seeding = not await _trigger_seen(account_id, trig.id)
        # One resolve per distinct owner, not per trigger — a person with
        # five triggers resolves their scope once.
        if trig.owner_user_id not in scopes:
            scopes[trig.owner_user_id] = await _owner_scope(
                tenant, account_id, trig.owner_user_id)
        scope = scopes[trig.owner_user_id]
        judged = 0
        for row in rows:
            vid = str(row.get("vehicle_id") or "")
            if not vid:
                continue
            if scope is not None and not scope.allows_row(
                    row, name_key="vehicle_name", external_key="vehicle_id"):
                continue        # not this person's truck — not their news
            reading = row.get(metric.column)
            if not cat.reading_usable(metric, reading, row.get("engine_state") or ""):
                stats["skipped"] += 1
                continue
            if is_stale(row.get("source_ts"), metric.stale_after_minutes):
                stats["skipped"] += 1
                continue

            judged += 1
            value = float(reading)
            key = _flag_key(account_id, trig.id, vid)
            was = await _in_breach(key)

            if cat.breaches(metric, value, trig.threshold):
                if was:
                    # Still in breach — already said, but RENEW the flag.
                    # Without this a condition outliving the flag's TTL
                    # (a long weekend at low fuel) would read as a fresh
                    # crossing and announce itself all over again, which
                    # is the exact thing this state exists to prevent.
                    await _set_breach(key, True)
                    continue
                await _set_breach(key, True)
                if seeding:
                    # A brand-new trigger inherits the fleet's current
                    # state instead of announcing all of it.
                    stats["seeded"] += 1
                    continue
                if await _deliver(account_id, trig, row, value,
                                  names.get(vid, "")):
                    stats["fired"] += 1
            elif was and cat.recovered(metric, value, trig.threshold):
                # Recovered past the band — re-armed.  v1 says nothing on
                # the way out; the transition is modelled because board
                # absorption will need it to auto-resolve later.
                await _set_breach(key, False)
                stats["cleared"] += 1

        if judged:
            # Only a sweep that actually READ something establishes the
            # baseline.  A trigger created while the whole fleet is
            # parked (for an engine-gated metric) would otherwise be
            # marked seen having seen nothing, and the first real
            # reading would arrive as a live alert instead of a seed.
            await _mark_trigger_seen(account_id, trig.id)
    return stats


async def _trigger_seen(account_id: int, trigger_id: int) -> bool:
    key = f"t:{account_id}:trigseen:{trigger_id}"
    if rcache.is_available():
        return await rcache.exists(key)
    return _local_flags.get(key, False)


async def _mark_trigger_seen(account_id: int, trigger_id: int) -> None:
    key = f"t:{account_id}:trigseen:{trigger_id}"
    if rcache.is_available():
        await rcache.setex_flag(key, _FLAG_TTL_SECONDS * 30)
    else:
        _local_flags[key] = True


async def _deliver(
    account_id: int, trig: AlertTrigger, row: dict[str, Any], value: float,
    vehicle_name: str = "",
) -> bool:
    """DM the trigger's owner.  Personal triggers write NO board row —
    the Alerts board is the account's shared queue, and one person's
    threshold is not the account's news."""
    from capabilities.notifications import NotificationContent, notify_user
    from capabilities.alerting.triggers.notification_category import TRIGGER_FIRED

    metric = trig.spec
    name = str(vehicle_name or row.get("vehicle_id") or "?")
    shown = int(value) if float(value).is_integer() else round(value, 1)
    try:
        await notify_user(
            get_platform_db(), account_id, trig.owner_user_id,
            NotificationContent(
                title=f"{name} — {metric.label.lower()} {metric.direction} {trig.threshold}{metric.unit}",
                body=f"Now {shown}{metric.unit}. Your alert trigger.",
                category=TRIGGER_FIRED,
                severity=trig.severity,
                meta={"trigger_id": trig.id, "vehicle_id": row.get("vehicle_id"),
                      "metric": trig.metric, "value": shown},
            ),
            channels=["in_app", "telegram_dm", "email"],
            correlation_key=f"trigger:{trig.id}:{row.get('vehicle_id')}",
        )
        return True
    except Exception as e:
        # Counted as NOT fired: a sweep log reading "fired=12" during a
        # notifications outage would look healthier than the truth.
        logger.warning("trigger %s: delivery failed for user %s: %s",
                       trig.id, trig.owner_user_id, e)
        return False


_tick = 0


@register_alert_source("alert_triggers_sweep", trigger="interval", minutes=SWEEP_MINUTES)
async def sweep_alert_triggers(app) -> None:
    """Every enabled trigger on the platform, grouped by account."""
    global _tick
    _tick += 1
    try:
        rows = await get_platform_db().list_enabled_alert_triggers()
    except Exception as e:
        logger.error("alert-trigger sweep: could not load triggers: %s", e)
        return
    if not rows:
        return

    by_account: dict[int, list[AlertTrigger]] = {}
    for row in rows:
        try:
            trig = AlertTrigger.from_row(row)
        except Exception:
            continue
        # A trigger naming a metric the catalog no longer carries is
        # inert, not an error: the row survives so a person can see and
        # delete it, but nothing is evaluated against a vocabulary that
        # has moved on.
        if trig.spec is None or trig.scope != "personal":
            continue
        by_account.setdefault(trig.account_id, []).append(trig)

    for account_id, triggers in by_account.items():
        tenant = await get_tenant_db(account_id)
        # run_account_job is not optional bookkeeping: it enters
        # ``tenant.with_account()``, which is what sets ``app.account_id``
        # for Postgres RLS.  Without it the sweep's query matches zero
        # rows under RLS and the whole feature goes quietly dead — no
        # error, no alert, nothing to see in the log.
        await run_account_job(
            _run_account(account_id, triggers, _tick),
            account_id=account_id, job_name="alert_triggers_sweep",
            tenant_db=tenant,
        )


async def _run_account(account_id: int, triggers, tick: int) -> None:
    stats = await evaluate_account(account_id, triggers, tick)
    if stats["fired"] or stats["seeded"]:
        logger.info(
            "alert-trigger sweep acct=%d triggers=%d fired=%d "
            "seeded=%d cleared=%d skipped=%d",
            account_id, len(triggers), stats["fired"],
            stats["seeded"], stats["cleared"], stats["skipped"])
