"""Vehicle state-snapshot history backfill from the upstream
provider's historical stats endpoint.

How this differs from ``backfill.py``
-------------------------------------
``backfill.py`` is the "fire every ingest job once" pass that runs
when a Samsara company is first connected — it populates the current-
snapshot tables (vehicle_state, vehicle_health_snapshot, etc.) and
the existing event logs.  Each ingestor reaches back its own window
(safety events: 7 days, driver efficiency: 30 days, etc.).

This module is different: it populates ``vehicle_state_snapshot``
(the per-vehicle time-series introduced by M1) by fetching genuine
historical samples from Samsara's ``/fleet/vehicles/stats/history``
endpoint and resampling them to the same 5-minute slot cadence the
live snapshot job uses going forward.

The maintenance calendar projection, cost-per-mile, utilisation
reports, and predictive maintenance composite all depend on the
snapshot history.  Without this backfill they sit dormant for the
first ~3 days after install; with it they work day one.

Execution model
---------------
A backfill is one long-running async task that:

  1. Acquires the global ``backfill_account_lock`` (M4) so only one
     account at a time runs across the whole platform.
  2. Reads the account's integration row to verify the integration
     is ``connected`` and the ``history_backfill`` toggle is on.
  3. Computes the list of UTC days inside the requested window that
     don't already have snapshot rows (resume cursor).
  4. For each remaining day:
     a. Fans out 3 paginated Samsara ``stats/history`` calls — one
        per batch of 4-stat-types-or-fewer (Samsara's per-call cap).
     b. Acquires a throttle token before EACH call so the sustained
        rate never exceeds the configured ``SAMSARA_BACKFILL_RPS``.
     c. Resamples the event-driven Samsara samples to one row per
        5-minute slot per vehicle (matches live snapshot cadence).
     d. Bulk-inserts via ``upsert_vehicle_state_snapshots`` —
        idempotent at the row level via ``ON CONFLICT DO NOTHING``.
     e. Sleeps ``SAMSARA_BACKFILL_DAY_CHUNK_SEC`` between days.
  5. Updates a per-account-per-provider Redis status key so the
     dashboard can render live progress.

Failure semantics
-----------------
A single day's fetch failure logs WARNING and continues with the
next day — one transient Samsara outage doesn't abort the whole
30-day backfill.  If the process itself dies mid-run (gunicorn
restart, OOM, etc.) the next invocation picks up from the last
incomplete day via the resume cursor.

Multi-provider readiness
------------------------
The function takes ``provider_id`` so future providers (Motive,
Geotab) plug in by registering their own ``get_stats_history``
implementation in the protocol.  Samsara is currently the only
provider with a known history surface; other providers either
return an empty dict (which produces an empty backfill — no error)
or implement their own equivalent endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from adapters.telematics.protocol import Capability
# Provider-agnostic backfill (driven by ``provider_id`` + ``get_telematics_client``),
# but the rate-limit throttle/lock + day-chunk size are still Samsara-shaped — Samsara
# is the only telematics provider today.  GENERALIZE WHEN A 2ND TELEMATICS PROVIDER
# LANDS: resolve the throttle/lock/chunk from the provider (e.g. via the protocol)
# instead of importing Samsara's directly.
from adapters.telematics.samsara.throttle import (
    backfill_account_lock,
    samsara_backfill_throttle,
)
from infra import cache as _redis
from infra import observability as _obs
from infra.config import SAMSARA_BACKFILL_DAY_CHUNK_SEC
from infra.platform import get_platform_db, get_tenant_db
from infra.services import get_telematics_client

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────

# Samsara caps ``/fleet/vehicles/stats/history`` at 4 type strings
# per call.  We need 11 stats to fully populate a snapshot row, so
# the backfill makes 3 paginated fetches per day per provider.  The
# heaviest stats (gps, engineStates) live in the third batch so the
# first two finish fast and downstream resampling can start while
# the third batch is still walking pagination.
STAT_BATCHES: tuple[list[str], ...] = (
    [
        "obdOdometerMeters",
        "obdEngineSeconds",
        "fuelPercents",
        "defLevelMilliPercent",
    ],
    [
        "batteryMilliVolts",
        "engineCoolantTemperatureMilliC",
        "engineOilPressureKPa",
        "engineLoadPercent",
    ],
    [
        "engineRpm",
        "engineStates",
        "gps",
    ],
)

# 5-minute resampling slot — matches the live snapshot cadence so
# backfilled rows align cleanly with rows written by the live job.
SLOT_SECONDS = 300

# Per-Samsara-stat-type, the ``vehicle_state_snapshot`` column +
# optional unit-converter.  Stat types that map to multiple columns
# (``gps`` → lat/lon/speed_mph) are handled specially below.
STAT_TYPE_TO_COLUMN: dict[str, tuple[str, Any]] = {
    "obdOdometerMeters":              ("odometer_mi",      lambda v: float(v) / 1609.344),
    "obdEngineSeconds":               ("engine_hours",     lambda v: float(v) / 3600.0),
    "fuelPercents":                   ("fuel_pct",         float),
    "defLevelMilliPercent":           ("def_pct",          lambda v: float(v) / 1000.0),
    "batteryMilliVolts":              ("battery_v",        lambda v: float(v) / 1000.0),
    "engineCoolantTemperatureMilliC": ("coolant_c",        lambda v: float(v) / 1000.0),
    "engineOilPressureKPa":           ("oil_psi",          lambda v: float(v) * 0.145038),
    "engineLoadPercent":              ("engine_load_pct",  float),
    "engineRpm":                      ("rpm",              float),
    # engineStates → engine_state.  Samsara returns literal
    # "On"/"Idle"/"Off"; we lowercase + map "On" → "moving" to match
    # the live ingest's vocabulary.
    "engineStates":                   ("engine_state",     None),
}

_ENGINE_STATE_MAP = {"on": "moving", "idle": "idle", "off": "off"}


# ── Result + status ──────────────────────────────────────────────

@dataclass
class BackfillResult:
    """Returned at end-of-run for caller visibility and logging.

    ``state`` is one of:
      * ``"completed"`` — every requested day finished (or was
        already populated and skipped).
      * ``"running"``   — published during the run via Redis; the
        final return value's state is never this.
      * ``"skipped"``   — preflight check failed (integration not
        connected, toggle disabled, no provider registered, lock
        contention).
      * ``"failed"``    — run aborted on an unrecoverable error
        before completing the day list.
    """

    state: str
    account_id: int
    provider_id: str
    days_requested: int
    days_done: int
    days_skipped_already_present: int
    rows_inserted: int
    api_calls: int
    elapsed_sec: float
    started_at: str
    finished_at: str
    reason: str = ""
    errors: list[str] = field(default_factory=list)


def _redis_key(
    account_id: int,
    provider_id: str,
    company_code: str | None = None,
) -> str:
    """Status-record Redis key.

    When ``company_code`` is set the key carries it as a suffix so
    a per-company "Refresh" run on the Integration card can co-exist
    with an account-wide "Run all" run without one clobbering the
    other's progress badge.  Backwards compatible with the original
    account-only shape when called without the company.
    """
    if company_code:
        return (
            f"backfill_history:{account_id}:{provider_id}:co={company_code}"
        )
    return f"backfill_history:{account_id}:{provider_id}"


# How long Redis state can sit at ``state="running"`` without a
# heartbeat refresh before we treat it as a dead task.  Picked at 5
# min because:
#   * a healthy backfill emits a heartbeat per per-day batch chunk
#     (every ~30-60s during the live loop)
#   * legitimate pauses between chunks max out around the M5 throttle
#     cooldown (SAMSARA_BACKFILL_DAY_CHUNK_SEC, ~10s)
#   * 5 min is well past any single legitimate gap but tight enough
#     that operators don't sit staring at a stale badge after a
#     restart kills the task
HEARTBEAT_STALE_SECONDS = 300


def _now_epoch() -> float:
    return time.time()


async def _publish_status(
    account_id: int,
    provider_id: str,
    payload: dict[str, Any],
    *,
    company_code: str | None = None,
) -> None:
    """Write the current backfill state into Redis for the dashboard
    poller.  Soft-fails when Redis is unavailable — backfill itself
    still works, the dashboard just won't see progress.

    Every publish stamps ``last_heartbeat`` (epoch seconds).  The
    status read path uses this to detect tasks that died without
    flipping state to ``failed`` (SIGKILL, OOM, API restart mid-
    flight) so operators don't sit staring at a stale ``running``
    badge for 24h.
    """
    try:
        if not _redis.is_available():
            return
        # Don't mutate the caller's dict — they reuse it.
        payload = {**payload, "last_heartbeat": _now_epoch()}
        await _redis.cache_set(
            _redis_key(account_id, provider_id, company_code),
            payload,
            ttl=86400,  # 24 hours
        )
    except Exception as e:
        logger.debug("backfill status publish failed acct=%d: %s", account_id, e)


async def get_backfill_status(
    account_id: int,
    provider_id: str,
    company_code: str | None = None,
) -> dict[str, Any] | None:
    """Read the current backfill state for the dashboard polling
    endpoint.  Returns None when there's no recorded state — the
    dashboard renders that as "no backfill running".

    Staleness coercion: if the record says ``state="running"`` but
    ``last_heartbeat`` is older than ``HEARTBEAT_STALE_SECONDS``,
    the task is presumed dead (SIGTERM, OOM, hung).  We coerce the
    returned shape to ``state="failed"`` with a clear reason so:
      * the dashboard badge clears
      * the TOCTOU re-trigger guard unblocks
      * the operator can re-run without manual Redis surgery

    The underlying Redis record isn't rewritten on read — the next
    write (heartbeat, completion, or explicit reset) will fix it.
    A pure read coerces only the returned shape.
    """
    try:
        if not _redis.is_available():
            return None
        payload = await _redis.get(
            _redis_key(account_id, provider_id, company_code),
        )
        if not isinstance(payload, dict):
            return payload  # None or unexpected — pass through
        if payload.get("state") == "running":
            hb = payload.get("last_heartbeat")
            try:
                hb_f = float(hb) if hb is not None else 0.0
            except (TypeError, ValueError):
                hb_f = 0.0
            # A record with NO heartbeat field is by definition pre-
            # heartbeat code — i.e. either it was written by an older
            # build (before this fix shipped) or the writer never got
            # to its first heartbeat publish.  Both cases mean the
            # task is presumed dead: a live writer would have stamped
            # one by now.  Treating missing-heartbeat as stale is what
            # auto-recovers the user's currently-stuck "4/28 days"
            # record without needing manual ``redis-cli DEL`` surgery.
            missing_heartbeat = hb_f == 0.0
            age_seconds = _now_epoch() - hb_f if hb_f else 0.0
            is_stale = missing_heartbeat or age_seconds > HEARTBEAT_STALE_SECONDS
            if is_stale:
                logger.warning(
                    "backfill state stale acct=%d provider=%s "
                    "missing_hb=%s age=%.0fs — coercing to failed",
                    account_id, provider_id, missing_heartbeat, age_seconds,
                )
                return {
                    **payload,
                    "state": "failed",
                    "reason": (
                        "stale heartbeat — task died mid-flight "
                        "(API restart, OOM, or worker crash)"
                    ),
                }
        return payload
    except Exception as e:
        logger.debug("backfill status read failed acct=%d: %s", account_id, e)
        return None


async def reset_backfill_status(
    account_id: int,
    provider_id: str,
    company_code: str | None = None,
) -> bool:
    """Owner-only escape hatch: forcibly clear the Redis state for a
    backfill — but only when it's not a successfully-completed run.

    Use case: heartbeat staleness coercion (5 min) handles most
    SIGTERM / OOM cases automatically, but an operator may want the
    badge gone immediately rather than waiting for the next status
    poll to coerce.

    Refuses to clear ``state="completed"`` records because those are
    the data the dashboard reads to render "Last backfill done · N
    rows" right after a run — an accidental Reset click would erase
    that legitimate badge.  ``running`` (with stale heartbeat),
    ``failed``, ``queued``, and ``skipped`` are all clearable.

    Returns True if a record existed AND was clearable AND got
    cleared.  False otherwise (no record / Redis unavailable /
    record is in a terminal-success state).
    """
    try:
        if not _redis.is_available():
            return False
        existing = await _redis.get(
            _redis_key(account_id, provider_id, company_code),
        )
        if not existing:
            return False
        prior_state = (
            existing.get("state") if isinstance(existing, dict) else None
        )
        if prior_state == "completed":
            logger.info(
                "backfill reset declined — record is completed "
                "acct=%d provider=%s", account_id, provider_id,
            )
            return False
        await _redis.delete(
            _redis_key(account_id, provider_id, company_code),
        )
        logger.info(
            "backfill state reset acct=%d provider=%s prior_state=%s",
            account_id, provider_id, prior_state or "?",
        )
        return True
    except Exception as e:
        logger.warning("backfill state reset failed acct=%d: %s", account_id, e)
        return False


# Chunk size for the shutdown sweep's parallel mark-as-failed.  Large
# enough that a few hundred keys finish in <1s on local Redis; small
# enough that 10k keys don't all fan out at once.
_SHUTDOWN_SWEEP_CHUNK = 50

# Overall budget for the shutdown sweep.  Uvicorn's SIGTERM grace
# period is typically ~10s; the lifespan hook also has to await
# background-task cancellation (5s) so the sweep gets the remainder.
_SHUTDOWN_SWEEP_TIMEOUT_SEC = 8.0


async def _mark_one_running_failed(key: str) -> bool:
    """Helper for ``mark_running_states_failed_on_shutdown`` — flips
    one key if it's still ``running``.  Idempotent; safe to race
    against the task's own finally clause."""
    try:
        payload = await _redis.get(key)
        if not isinstance(payload, dict):
            return False
        if payload.get("state") != "running":
            return False
        await _redis.cache_set(
            key,
            {
                **payload,
                "state": "failed",
                "reason": "api restart in flight",
                "finished_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds",
                ),
                "last_heartbeat": _now_epoch(),
            },
            ttl=86400,
        )
        return True
    except Exception as e:
        logger.debug("shutdown sweep: failed key=%s: %s", key, e)
        return False


async def mark_running_states_failed_on_shutdown() -> int:
    """SCAN every ``backfill_history:*`` key.  For any record still
    marked ``running``, flip it to ``failed`` with reason='api
    restart in flight' so on next boot the dashboard reflects the
    real state instead of a stale progress bar.

    Called from the FastAPI lifespan shutdown hook AFTER cancelling
    the in-flight background tasks.  Bounded by
    ``_SHUTDOWN_SWEEP_TIMEOUT_SEC`` so a slow Redis can't extend
    uvicorn's SIGTERM grace period.  Per-key updates fan out in
    chunks of ``_SHUTDOWN_SWEEP_CHUNK`` so 10k keys don't serialize
    20k round-trips.  Returns the count of records marked failed.
    """
    if not _redis.is_available():
        return 0
    try:
        keys = await asyncio.wait_for(
            _redis.scan_keys("backfill_history:*"),
            timeout=_SHUTDOWN_SWEEP_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "shutdown sweep: SCAN timed out — leaving Redis state untouched",
        )
        return 0
    except Exception as e:
        logger.warning("shutdown sweep: SCAN failed: %s", e)
        return 0

    marked = 0
    try:
        async def _sweep_all() -> int:
            n = 0
            for i in range(0, len(keys), _SHUTDOWN_SWEEP_CHUNK):
                chunk = keys[i:i + _SHUTDOWN_SWEEP_CHUNK]
                results = await asyncio.gather(
                    *(_mark_one_running_failed(k) for k in chunk),
                    return_exceptions=True,
                )
                n += sum(1 for r in results if r is True)
            return n

        marked = await asyncio.wait_for(
            _sweep_all(),
            timeout=_SHUTDOWN_SWEEP_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "shutdown sweep: chunked mark-failed timed out "
            "after %.0fs — partial sweep, marked=%d",
            _SHUTDOWN_SWEEP_TIMEOUT_SEC, marked,
        )

    if marked:
        logger.info(
            "shutdown sweep: marked %d in-flight backfill record(s) as failed",
            marked,
        )
    return marked


# ── Resampling ───────────────────────────────────────────────────

def _floor_to_slot(ts: datetime) -> str:
    """Snap a timestamp down to the nearest 5-minute UTC slot
    boundary, returned as the canonical ISO string used as
    ``captured_at``."""
    aligned = ts.replace(
        minute=(ts.minute // 5) * 5, second=0, microsecond=0,
    )
    return aligned.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_sample_time(t: str) -> datetime | None:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resample_to_snapshot_rows(
    per_vehicle_samples: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert per-vehicle per-stat-type samples into snapshot rows
    aligned to 5-minute slots.

    ``per_vehicle_samples`` is the dict the provider's
    ``get_stats_history`` returns, merged across the three stat
    batches: ``{vehicle_id: {stat_type: [{time, value}, ...], ...}}``.

    The output is one row per (vehicle, slot) pair that has at least
    one observed sample.  Within a slot, multiple samples for the
    same stat collapse to the LAST one (insertion order) — the
    snapshot represents the truck's state at the end of the slot.
    """
    by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for vid, stat_lists in per_vehicle_samples.items():
        if not vid:
            continue
        for stat_type, samples in (stat_lists or {}).items():
            if stat_type in ("name", "_org"):
                continue
            if stat_type == "gps":
                _accumulate_gps(by_slot, vid, samples or [])
                continue
            mapping = STAT_TYPE_TO_COLUMN.get(stat_type)
            if not mapping:
                continue
            column, converter = mapping
            for sample in samples or []:
                t = _parse_sample_time(sample.get("time", ""))
                if t is None:
                    continue
                slot_key = (vid, _floor_to_slot(t))
                row = by_slot.setdefault(slot_key, {
                    "vehicle_id": vid,
                    "captured_at": slot_key[1],
                })
                raw = sample.get("value")
                if raw is None:
                    continue
                if column == "engine_state":
                    row[column] = _ENGINE_STATE_MAP.get(
                        str(raw).strip().lower(), "off",
                    )
                else:
                    try:
                        row[column] = converter(raw) if converter else raw
                    except (TypeError, ValueError):
                        continue
    return list(by_slot.values())


def _accumulate_gps(
    by_slot: dict[tuple[str, str], dict[str, Any]],
    vehicle_id: str,
    samples: list[dict[str, Any]],
) -> None:
    """GPS samples fan out into three columns (lat / lon / speed_mph)
    so they need their own resampling path — one sample contributes
    to multiple snapshot columns at once."""
    for s in samples:
        t = _parse_sample_time(s.get("time", ""))
        if t is None:
            continue
        slot_key = (vehicle_id, _floor_to_slot(t))
        row = by_slot.setdefault(slot_key, {
            "vehicle_id": vehicle_id,
            "captured_at": slot_key[1],
        })
        value = s.get("value") or {}
        if isinstance(value, dict):
            if value.get("latitude") is not None:
                row["lat"] = float(value["latitude"])
            if value.get("longitude") is not None:
                row["lon"] = float(value["longitude"])
            if value.get("speedMilesPerHour") is not None:
                row["speed_mph"] = float(value["speedMilesPerHour"])


def _merge_batch(
    accumulator: dict[str, dict[str, Any]],
    batch_result: dict[str, dict[str, Any]],
) -> None:
    """Fold a single ``get_stats_history`` response into the running
    per-vehicle merged dict.  Lists for the same (vehicle, stat_type)
    get concatenated — they shouldn't overlap because each batch
    covers different stat types."""
    for vid, payload in batch_result.items():
        target = accumulator.setdefault(vid, {})
        for k, v in (payload or {}).items():
            if k in ("name", "_org"):
                target[k] = v
                continue
            existing = target.get(k)
            if existing is None:
                target[k] = list(v) if isinstance(v, list) else v
            elif isinstance(existing, list) and isinstance(v, list):
                existing.extend(v)


# ── Main capability ──────────────────────────────────────────────

async def backfill_vehicle_history(
    account_id: int,
    *,
    days: int = 30,
    provider_id: str = "samsara",
    triggered_by: int = 0,
    company_code: str | None = None,
) -> BackfillResult:
    """Backfill ``vehicle_state_snapshot`` from the provider's
    historical stats endpoint.

    Idempotent and resumable — re-running over the same window skips
    days that already have rows.  Locked globally so two accounts
    can't run simultaneously (M4 ``backfill_account_lock``).

    ``company_code`` (optional) restricts the run to a single company.
    Used by the per-company "Refresh" button on the Integration card —
    progress lands under a per-company Redis key so a row-level refresh
    can co-exist with an in-flight "Run all".  The day-skip optimisation
    is bypassed when this is set: per-company refresh always re-fetches
    the requested window for that company even if account-wide rows
    already exist for those days (because they may be the OTHER
    companies' data, not this one's).

    Returns a ``BackfillResult`` summarising the run.  Callers can
    persist it to a job-history table or log it; the dashboard reads
    progress from Redis via ``get_backfill_status``.
    """
    started_at_dt = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    started_at_iso = started_at_dt.isoformat(timespec="seconds")

    # Every early-return path produces a well-shaped object so the
    # caller never has to introspect None.
    result = BackfillResult(
        state="skipped",
        account_id=account_id,
        provider_id=provider_id,
        days_requested=days,
        days_done=0,
        days_skipped_already_present=0,
        rows_inserted=0,
        api_calls=0,
        elapsed_sec=0.0,
        started_at=started_at_iso,
        finished_at=started_at_iso,
    )

    # Cross-account serial lock — the trigger endpoint is fire-and-
    # forget and we don't want a queued backfill to hold an HTTP
    # handler open.  If the lock is contended, return early with a
    # ``skipped`` result.
    if backfill_account_lock.locked():
        result.reason = (
            "another account backfill is in progress — try again later"
        )
        # Same-account re-entry guard: if THIS account already has a
        # task running and publishing progress, writing ``state=
        # "skipped"`` to the SAME Redis key would clobber the live
        # task's ``state="running"`` payload — the dashboard badge
        # then stops polling (refetchInterval only fires on running/
        # queued) and the live progress goes invisible.
        #
        # Sources of the same-account re-entry: auto-trigger-on-
        # connect racing a manual click, two dashboard tabs, double-
        # click before the TOCTOU guard flips state to ``queued``.
        # The endpoint preflight should catch most of these, but
        # this defensive check protects the in-flight task's status
        # from being trampled even when the preflight loses the race.
        #
        # Cross-account skipped is still published normally — the
        # OTHER account's user genuinely needs the ``skipped`` signal
        # so they know to retry later.
        #
        # CRITICAL: read the RAW Redis payload here, NOT via
        # ``get_backfill_status``.  That helper applies heartbeat
        # staleness coercion (running + stale heartbeat -> failed in
        # the returned shape); if we used the coerced view, a long-
        # running backfill that paused for >5min would look "failed"
        # to our suppression check, we'd publish 'skipped', and the
        # original record (still being actively written by the live
        # task) would get clobbered the moment the task resumes.
        # Reading the raw payload preserves the "is anything actually
        # in the slot?" signal we need for clobber detection.
        try:
            if _redis.is_available():
                existing = await _redis.get(
                    _redis_key(account_id, provider_id, company_code),
                )
            else:
                existing = None
            same_account_active = (
                isinstance(existing, dict)
                and existing.get("state") in ("running", "queued")
            )
        except Exception as e:
            # Degraded Redis: we have no evidence of a live task, so
            # publish normally.  A live writer would just overwrite
            # this on its next heartbeat.  Logged so ops can correlate.
            logger.warning(
                "backfill skip suppression: raw status read failed "
                "acct=%d: %s — publishing skipped anyway", account_id, e,
            )
            same_account_active = False
        if not same_account_active:
            await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
        else:
            logger.info(
                "backfill skip suppressed acct=%d provider=%s — would have "
                "clobbered live %s state",
                account_id, provider_id, existing.get("state"),
            )
        return result

    async with backfill_account_lock:
        try:
            db = get_platform_db()
            ai = await db.get_account_integration(account_id, provider_id)
            if ai is None:
                result.reason = f"no {provider_id} integration configured"
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result
            if ai.status != "connected":
                result.reason = (
                    f"integration status is {ai.status!r}, not connected"
                )
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result
            cap_cfg = ai.feature_toggles.get(Capability.HISTORY_BACKFILL) or {}
            if not cap_cfg.get("enabled", False):
                result.reason = "history_backfill toggle is disabled"
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result

            try:
                provider = await get_telematics_client(account_id, provider_id)
            except KeyError as e:
                result.reason = f"provider not registered: {e}"
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result

            # Nothing to fetch when the account has zero companies with
            # API keys — the per-day loop would walk the whole window
            # fetching empty batches and finish as "completed · 0 rows",
            # which reads like the backfill worked but found no data.
            # A fresh account connecting Samsara before adding companies
            # hits this on the auto-triggered first-connect backfill.
            # Skip with an actionable reason instead.  ``getattr`` chain
            # keeps this provider-generic — only providers whose client
            # exposes a per-company ``clients`` map are checked.
            company_clients = getattr(
                getattr(provider, "client", None), "clients", None,
            )
            if company_clients is not None and len(company_clients) == 0:
                result.reason = (
                    "no companies with API keys yet — add your companies "
                    "and their keys, then run Refresh history"
                )
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result

            tenant = await get_tenant_db(account_id)
            if tenant is None:
                result.reason = "tenant DB unavailable"
                await _publish_status(account_id, provider_id, asdict(result), company_code=company_code)
                return result

            # Day cursor — newest-first within the requested window,
            # skipping days that already have any snapshot row.  The
            # at-or-above-0-rows skip is intentional: partial days
            # re-fetch cheaply because the row-level idempotency
            # short-circuits duplicates.
            #
            # Per-company refresh bypasses the skip: the per-account
            # ``vehicle_state_snapshot_has_day`` predicate can't tell
            # WHICH company already has data, and the operator clicked
            # Refresh on a specific company expecting that company's
            # data to be fetched.  Skipping would leave them clicking
            # a button that quietly does nothing.
            today = datetime.now(timezone.utc).date()
            days_to_run: list[date] = []
            for offset in range(days, 0, -1):
                d = today - timedelta(days=offset)
                if (
                    company_code is None
                    and await tenant.vehicle_state_snapshot_has_day(account_id, d)
                ):
                    result.days_skipped_already_present += 1
                else:
                    days_to_run.append(d)

            result.state = "running"
            await _publish_status(
                account_id, provider_id,
                {
                    **asdict(result),
                    "days_total": len(days_to_run),
                    "triggered_by": triggered_by,
                },
                company_code=company_code,
            )

            # Per-day chunk loop.
            for day_index, day in enumerate(days_to_run):
                start_dt = datetime.combine(
                    day, datetime.min.time(), tzinfo=timezone.utc,
                )
                end_dt = start_dt + timedelta(days=1)
                start_iso = start_dt.isoformat()
                end_iso = end_dt.isoformat()

                # Fetch each stat batch — per-batch errors get logged
                # and skipped because partial-day data is still useful.
                merged: dict[str, dict[str, Any]] = {}
                for batch in STAT_BATCHES:
                    try:
                        await samsara_backfill_throttle.acquire()
                        batch_result = await provider.get_stats_history(
                            batch, start_iso, end_iso,
                            company_code=company_code,
                        )
                        result.api_calls += 1
                        _merge_batch(merged, batch_result)
                    except Exception as e:
                        err = (
                            f"day={day.isoformat()} "
                            f"batch={batch[:2]}...: {e}"
                        )
                        logger.warning("backfill batch failed: %s", err)
                        result.errors.append(err)
                    # Mid-day heartbeat: re-publish progress after each
                    # batch so a single slow day (huge fleet, paginated
                    # response, retries) doesn't starve the 5-min
                    # staleness timer and false-trip the dashboard
                    # into thinking the task died.  Cheap — one Redis
                    # SET per batch, ~3 batches per day, ~1KB payload.
                    await _publish_status(
                        account_id, provider_id,
                        {
                            **asdict(result),
                            "days_total": len(days_to_run),
                            "triggered_by": triggered_by,
                        },
                        company_code=company_code,
                    )

                # Resample + persist this day's data.
                try:
                    rows = _resample_to_snapshot_rows(merged)
                    if rows:
                        n = await tenant.upsert_vehicle_state_snapshots(
                            account_id, rows,
                        )
                        result.rows_inserted += n
                except Exception as e:
                    err = f"day={day.isoformat()} persist: {e}"
                    logger.exception("backfill persist failed: %s", err)
                    result.errors.append(err)

                result.days_done = day_index + 1
                await _publish_status(
                    account_id, provider_id,
                    {
                        **asdict(result),
                        "days_total": len(days_to_run),
                        "triggered_by": triggered_by,
                    },
                    company_code=company_code,
                )

                # Cooldown between day chunks; skip after last day.
                if day_index + 1 < len(days_to_run):
                    await asyncio.sleep(SAMSARA_BACKFILL_DAY_CHUNK_SEC)

            result.state = "completed"

            # Stamp ``last_backfill_at`` so the dashboard integration
            # card can render "Last backfill: 2026-06-07 14:22 UTC"
            # persistently — operators don't have to wait for the
            # 24h Redis status badge to expire to know data is fresh.
            #
            # Skip for per-company refreshes: only "Run all" represents
            # account-wide freshness.  Otherwise refreshing ONE company
            # would mis-stamp the badge as if the WHOLE account had
            # been refreshed.
            if company_code is None:
                try:
                    db = get_platform_db()
                    await db.record_integration_backfill_completion(
                        account_id, provider_id,
                    )
                except Exception:
                    # Non-fatal — the timestamp is a UX nicety, not a
                    # correctness requirement.  Log for ops visibility.
                    logger.exception(
                        "record_integration_backfill_completion failed "
                        "acct=%d provider=%s", account_id, provider_id,
                    )

            # Chain hourly + daily aggregations so the calendar
            # projection's median path (which reads from
            # ``vehicle_metrics_daily``) has data the moment this
            # backfill finishes — otherwise the operator would see
            # snapshot rows but the calendar would still be empty
            # for ~7 more days while the live aggregators caught up.
            try:
                from capabilities.warehouse.aggregator import (
                    backfill_aggregations,
                )
                agg = await backfill_aggregations(account_id, days=days)
                logger.info(
                    "backfill acct=%d provider=%s aggregations rolled up: "
                    "hours=%d days=%d hourly_rows=%d daily_rows=%d",
                    account_id, provider_id,
                    agg["hours"], agg["days"],
                    agg["hourly_rows"], agg["daily_rows"],
                )
            except Exception as e:
                # Aggregation failure shouldn't flip the M5 result
                # to "failed" — the snapshot data is still useful and
                # the live cron will catch up over the next 7 days.
                result.errors.append(f"aggregations: {e}")
                logger.exception(
                    "backfill_aggregations chain failed acct=%d", account_id,
                )
        except asyncio.CancelledError:
            # Lifespan shutdown is cancelling us — flip to a coherent
            # terminal state BEFORE the finally publishes.  Without
            # this, ``state`` is still ``"running"`` from the per-day
            # loop's last publish, and the finally would re-stamp that
            # to Redis AFTER the shutdown sweep had already marked it
            # ``"failed"`` (sweep runs sequentially after cancellation
            # in the lifespan hook).  Net result on next boot: badge
            # back to ``"running"``, badge stuck for 5 min until the
            # staleness coercion catches it.  Catching here turns the
            # race into a clean handoff.  Re-raise so asyncio.gather
            # still sees the cancellation.
            result.state = "failed"
            result.reason = "cancelled (api shutdown)"
            logger.info(
                "backfill cancelled by shutdown acct=%d provider=%s "
                "days_done=%d", account_id, provider_id, result.days_done,
            )
            raise
        except Exception as e:
            result.state = "failed"
            result.errors.append(f"top-level: {e}")
            logger.exception(
                "backfill aborted unexpectedly acct=%d", account_id,
            )
        finally:
            result.elapsed_sec = round(time.perf_counter() - t0, 1)
            result.finished_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            )
            await _publish_status(
                account_id, provider_id,
                {
                    **asdict(result),
                    "triggered_by": triggered_by,
                },
                company_code=company_code,
            )
            logger.info(
                "backfill acct=%d provider=%s state=%s days_done=%d "
                "rows=%d api_calls=%d elapsed=%.1fs",
                account_id, provider_id, result.state,
                result.days_done, result.rows_inserted,
                result.api_calls, result.elapsed_sec,
            )
            # ``record_integration_backfill_run`` is an optional
            # Prometheus shim that may or may not be defined in the
            # observability module depending on the deploy.  Using
            # getattr keeps history_backfill.py importable in tests
            # / minimal builds that don't ship the metrics registry.
            _record = getattr(_obs, "record_integration_backfill_run", None)
            if callable(_record):
                _record(provider_id, result.state)

    return result
