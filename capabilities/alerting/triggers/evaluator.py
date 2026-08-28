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


class _AllOf:
    """Every wall must allow the row.

    Composed rather than intersected into one id set, because each wall
    decides on the strongest rung IT carries — the company wall knows
    registry ids, the driver ladder also knows external ids and names.
    Flattening them into a single set would silently demote whichever
    rung the other one lacked.
    """

    def __init__(self, scopes):
        self._scopes = [s for s in scopes if s is not None]

    def allows_row(self, row, **kw) -> bool:
        return all(s.allows_row(row, **kw) for s in self._scopes)


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

        # THE COMPANY WALL, and it applies to every role.  The driver
        # ladder below narrows one assigned person; this narrows everyone
        # else, and without it the sweep DM'd a company-restricted
        # dispatcher the display name and fuel/DEF/battery/oil reading of
        # trucks in companies they cannot open anywhere in the product.
        # ``_deliver`` calls notify_user directly rather than send_alert,
        # so it never passed through filter_subscribers_by_company —
        # the gate every other alert path applies.
        walls = []
        codes = await db.get_user_company_codes(owner_user_id)
        if codes:
            ph = ", ".join("?" for _ in codes)
            cur = await tenant._db.execute(
                f"SELECT id FROM vehicles WHERE account_id = ? "
                f"  AND company_code IN ({ph})",
                (account_id, *codes),
            )
            from capabilities.permissions.vehicle_scope import VehicleScope
            walls.append(VehicleScope(
                registry_ids=frozenset(int(r[0]) for r in await cur.fetchall())))

        if role != "driver":
            return _AllOf(walls) if walls else None
        trucks = await db.get_user_vehicle_nums(owner_user_id)
        if not trucks:
            # Legacy behaviour, kept deliberately and matching
            # deps.get_user_vehicle_scope: a driver with NO assignment at
            # all is unrestricted rather than blind.
            trucks = [getattr(user, "truck_num", "")] if getattr(user, "truck_num", "") else []
        if not trucks:
            return _AllOf(walls) if walls else None
        from capabilities.permissions.vehicle_scope import build_vehicle_scope
        walls.append(await build_vehicle_scope(tenant, account_id, trucks))
        return _AllOf(walls)
    except Exception as e:
        # Fail CLOSED for a restricted owner is not possible without
        # knowing they are restricted — so an unresolvable scope means we
        # cannot prove the person may see these trucks, and the trigger
        # stays silent this sweep rather than guessing.
        logger.warning("trigger scope unresolved for user %s: %s",
                       owner_user_id, e)
        return _DENY_ALL


async def _target_scope(tenant, account_id: int, ids: list[int]):
    """The vehicles a trigger explicitly targets, as a VehicleScope.

    Deliberately the SAME type the owner wall uses, so targeting is
    matched by the same identity ladder rather than a second comparison
    that could drift from it.  Ids are ``vehicles.id``; the provider ids
    are resolved HERE, on every sweep, which is what makes a target
    survive a gateway swap: the registry row keeps its id, its
    ``telematics_ref`` is rewritten in place, and the next sweep picks up
    the new one.

    Scoped by ``account_id`` in the WHERE as well as by RLS — a stored id
    from another tenant matches nothing rather than resolving.

    Returns ``_DENY_ALL`` when a non-empty selection resolves to nothing.
    A trigger that names vehicles is a trigger that asked to be narrow;
    if none of them can be resolved, firing on the whole fleet would be
    the opposite of what was asked.
    """
    if not ids:
        return None                      # "all of my scope" — no narrowing
    try:
        placeholders = ", ".join("?" for _ in ids)
        # `is_active = 1`: a retired truck must not resolve into the
        # allow-set, or a trigger someone pointed at three trucks keeps
        # firing on the one that left.  BOTH kinds of retirement are
        # silent here — unlike the ingest gate, which must let a
        # sweep-retired badge back in when it reports again; nothing
        # about alerting wants that distinction.
        # The picker that BUILT this selection already filters the same
        # way (triggers/router.py), so this closes the gap between what
        # the UI offers and what the sweep judges.
        cur = await tenant._db.execute(
            f"SELECT id, telematics_ref FROM vehicles "
            f"WHERE account_id = ? AND is_active = 1 "
            f"AND id IN ({placeholders})",
            (account_id, *ids),
        )
        rows = await cur.fetchall()
    except Exception as e:
        logger.warning("trigger targets unresolved acct=%s: %s", account_id, e)
        return _DENY_ALL
    registry_ids = {int(r[0]) for r in rows}
    external_ids = {str(r[1]) for r in rows if r[1]}
    if not registry_ids:
        return _DENY_ALL
    from capabilities.permissions.vehicle_scope import VehicleScope
    # No ``names`` rung on purpose: a target is an identity, and falling
    # back to a name is how "230" once matched 2303.
    return VehicleScope(registry_ids=frozenset(registry_ids),
                        external_ids=frozenset(external_ids))


def _collapse_by_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per VEHICLE, where the query gave one row per DEVICE.

    ``_latest_per_vehicle`` dedupes on ``DISTINCT ON (vehicle_id)`` — the
    PROVIDER id — so a truck whose gateway was swapped appears twice: the
    new device, and the retired one whose last reading stays judgeable
    until the metric's staleness bar passes (24h for fuel and DEF).  Both
    rows carry the same ``registry_id``, each gets its own crossing flag,
    and the same truck can announce itself twice.

    This is live, not hypothetical: registry id 99 on the production
    account maps to two provider ids today.

    Collapsed in Python rather than by changing the SQL — the DISTINCT ON
    is what makes this one indexed query per account, and rows with a
    NULL ``registry_id`` (a vehicle the registry has not placed yet) are
    passed through untouched rather than being merged into one bucket.
    """
    best: dict[int, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        rid = row.get("registry_id")
        if rid is None:
            out.append(row)              # unplaced — cannot be collapsed
            continue
        rid = int(rid)
        prev = best.get(rid)
        # Lexical comparison, and that is safe rather than lucky: every
        # one of the 1,026,473 source_ts values on the live account is
        # exactly ``YYYY-MM-DDTHH:MM:SSZ`` — fixed width, zero-padded,
        # single UTC suffix — so string order IS chronological order.  A
        # writer that ever emits an offset (``+00:00``) or fractional
        # seconds breaks that silently, which is why the shape is stated
        # here rather than assumed.
        if prev is None or str(row.get("source_ts") or "") > str(prev.get("source_ts") or ""):
            best[rid] = row
    out.extend(best.values())
    return out


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
    # One row per vehicle, not per device — see _collapse_by_registry.
    rows = _collapse_by_registry(rows)
    names = await _names_for(tenant, account_id)

    scopes: dict[int, Any] = {}
    # One resolve per distinct SELECTION, not per trigger: two triggers
    # naming the same three trucks cost one query, and the untargeted
    # majority cost none at all.
    targets: dict[str, Any] = {}
    for trig in due:
        metric = trig.spec
        seeding = not await _trigger_seen(account_id, trig.id)
        # One resolve per distinct owner, not per trigger — a person with
        # five triggers resolves their scope once.
        if trig.owner_user_id not in scopes:
            scopes[trig.owner_user_id] = await _owner_scope(
                tenant, account_id, trig.owner_user_id)
        scope = scopes[trig.owner_user_id]
        if trig.vehicles not in targets:
            targets[trig.vehicles] = await _target_scope(
                tenant, account_id, trig.target_ids)
        target = targets[trig.vehicles]
        judged = 0
        for row in rows:
            vid = str(row.get("vehicle_id") or "")
            if not vid:
                continue
            if scope is not None and not scope.allows_row(
                    row, name_key="vehicle_name", external_key="vehicle_id"):
                continue        # not this person's truck — not their news
            # Targeting NARROWS, never widens: a second, independent
            # allows_row on top of the owner wall.  ANDed as two calls
            # rather than intersecting the two scopes' id sets, because
            # each side decides on the strongest rung IT carries and an
            # intersection would silently drop a vehicle the two sides
            # know by different rungs.
            if target is not None and not target.allows_row(
                    row, name_key="vehicle_name", external_key="vehicle_id"):
                continue        # watched, but not one of the chosen
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


async def forget_trigger_state(account_id: int, trigger_id: int) -> None:
    """Drop everything the sweep remembers about one trigger.

    Called when a trigger's WATCH changes shape — its threshold moved, or
    its vehicle list did.  Both invalidate the crossing flags for the
    same reason: a flag says "this pair was already in breach last time I
    looked", and that sentence is only meaningful against the watch that
    set it.  Remove a vehicle and add it back inside the flag's 24h TTL
    and it would still read as in-breach, silently swallowing its next
    real crossing.

    Clearing the seen-marker too means the next sweep RE-SEEDS: it
    re-learns the fleet's current state and announces none of it.  That
    is deliberately the same promise the UI already makes about a new
    trigger — you hear on the crossing, not on what was already true.
    """
    prefix = f"t:{account_id}:trig:{trigger_id}:"
    seen_key = f"t:{account_id}:trigseen:{trigger_id}"
    if rcache.is_available():
        try:
            keys = await rcache.scan_keys(f"{prefix}*")
            for key in keys:
                await rcache.delete(key)
            await rcache.delete(seen_key)
        except Exception as e:
            # A stale flag re-seeds at worst; failing the edit the person
            # just made would be the larger harm.
            logger.warning("trigger %s: could not clear state: %s", trigger_id, e)
        return
    for key in [k for k in _local_flags if k.startswith(prefix)]:
        _local_flags.pop(key, None)
    _local_flags.pop(seen_key, None)


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
                # Everything the Triggers tab renders as COLUMNS, written
                # at fire time rather than looked up at read time: the
                # trigger can be edited or deleted afterwards, and a
                # history that re-reads today's threshold would silently
                # rewrite what it said last week.
                meta={"trigger_id": trig.id, "vehicle_id": row.get("vehicle_id"),
                      "vehicle": name, "metric": trig.metric,
                      "threshold": trig.threshold, "value": shown},
            ),
            # This trigger's own channels, bell always first.  Not the
            # notification matrix: that has one row for every trigger a
            # person owns, which cannot say "DEF reaches my phone, battery
            # can wait for email".
            channels=trig.delivery_channels,
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
