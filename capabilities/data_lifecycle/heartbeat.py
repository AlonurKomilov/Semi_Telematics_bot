"""The bot process's pulse, readable from any process.

Lives here — beside ``staleness``, which it already leaned on — and
not with the capacity sampler that WRITES the row it reads: the reader
is a plain age-of-newest-row over ``system_metrics_minute``, and the
ingest router, the API's /health and the operator console all need it.
Two of those live below the system layer, and a reader that lived
with the sampler would have made them import their watcher.
"""

from __future__ import annotations


async def bot_heartbeat_age_min(db) -> float | None:
    """Minutes since the sampler last wrote a minute row — the BOT
    process's pulse, readable from any process.

    The sampler runs inside the bot scheduler every 60s, so the newest
    ``system_metrics_minute.ts`` doubles as a cross-process liveness
    signal: the API's ``/health`` reports it so an EXTERNAL watcher can
    detect a dead bot even while the API itself is fine — the one
    failure every in-process alerter is structurally blind to.
    Returns None when unknown (no rows yet / probe failed).
    """
    from capabilities.data_lifecycle.staleness import data_age_minutes

    try:
        cur = await db._db.execute("SELECT MAX(ts) FROM system_metrics_minute")
        row = await cur.fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    ts = str(row[0])
    if len(ts) == 16:          # sampler stamps minute precision, UTC, no suffix
        ts += ":00+00:00"
    return data_age_minutes(ts)
