"""Fill ``poi_points`` from OSM — the work the map used to do per pan.

The six Overpass-backed layers describe things that do not move, so they
are fetched on a schedule and served from our own table.  This is the
fetch.  (The map draws seven built-in layers; repair shops are the
seventh and come from ``vendor_directory``, which was already ours.)

WHAT A JOB MAY DO THAT A REQUEST MAY NOT: wait.  The request path holds
itself to fifty seconds because nginx answers for it at sixty; nobody is
waiting on this one, so it asks for a whole region at a time and gives
the mirror minutes to find a slot.  That is the trade the whole design
turns on — a few big patient queries once a week, instead of many small
impatient ones on every pan.

WHAT IT WILL NOT DO: leave a layer emptier than it found it.  A region
that never answered fails the whole layer's run, and a failed run sweeps
nothing and moves no date (``finish_poi_import``).  Last week's truck
stops are a far better answer than none, and this codebase has paid for
the other one more than once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import overpass, viewport
from .layers import POI_OVERPASS_QUERIES

logger = logging.getLogger(__name__)

#: What we ask the mirror to allow itself, in its own query header.  The
#: public instances cap this; asking for more than they allow is refused
#: outright, so this is deliberately inside the common ceiling.
_SERVER_TIMEOUT_S = 180

#: Our own patience per attempt, and how many attempts one region gets.
#: Three tries an hour apart would be a different job; this is three
#: tries a minute apart, because what is being waited out is a queue.
_ATTEMPT_S = 200
_ATTEMPTS = 3
_PAUSE_S = 60

#: Between layers, so one import does not arrive as a burst.  A weekly
#: job has all the time in the world and the mirrors are volunteers.
_BETWEEN_LAYERS_S = 30

#: When to stop, because the source is DOWN rather than busy.
#:
#: Every region gets three attempts a minute apart, so a layer whose
#: mirror is refusing costs about half an hour and returns nothing.  Six
#: of those is most of a working day spent learning one fact, and the
#: first two layers have already established it.  Measured while the
#: owner watched the first real run: HTTP 504 after eighty seconds,
#: every attempt.
#:
#: Stopping is also the HONEST outcome — a failed layer changes nothing,
#: so an abandoned run leaves exactly what a completed failing one would
#: have, hours earlier.
_GIVE_UP_AFTER_DEAD_LAYERS = 2


def _region_query(layer: str, bbox: str) -> str:
    """The same clauses the map asks for, over a whole region.

    Built from POI_OVERPASS_QUERIES so a layer added there is imported
    without a second edit here — the registry stays the one place that
    says what a layer IS.
    """
    parts = "\n  ".join(f"{p}({bbox});" for p in POI_OVERPASS_QUERIES[layer])
    return (
        f"[out:json][timeout:{_SERVER_TIMEOUT_S}];\n"
        f"(\n  {parts}\n);\n"
        f"out center;"
    )


async def _fetch_region(layer: str, bbox: str) -> list[dict]:
    """One region's points, or raise after the last attempt."""
    last: Exception = RuntimeError("not attempted")
    for attempt in range(_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_PAUSE_S)
        try:
            data = await overpass._overpass_post(
                _region_query(layer, bbox),
                timeout=_ATTEMPT_S,
                # The budget a request may not raise, raised — see
                # _overpass_post's docstring.  Two mirrors x one attempt
                # each, with room to spare.
                budget=_ATTEMPT_S * 2 + 10,
            )
        except Exception as exc:
            last = exc
            logger.warning("poi import: %s %s attempt %d/%d: %s",
                           layer, bbox, attempt + 1, _ATTEMPTS, exc)
            continue
        # Keyed rather than appended: a union query overlaps on purpose
        # (a Pilot tagged fuel:diesel=yes also matches the brand list),
        # and the table would collapse the duplicates anyway — this just
        # saves writing each of them twice.
        points: dict[tuple[str, int], dict] = {}
        for element in data.get("elements") or []:
            p = overpass.element_to_point(element)
            if p is None or p.get("osm_id") is None:
                continue
            points[(p["osm_type"], int(p["osm_id"]))] = p
        return list(points.values())
    raise last


async def import_layer(db, layer: str, stamp: str | None = None) -> dict:
    """Refresh one built-in layer across the whole USA.

    Returns ``{layer, points, ok, note}``.  Never raises: a layer that
    could not be imported is a logged fact, not a dead scheduler.
    """
    if layer not in POI_OVERPASS_QUERIES:
        return {"layer": layer, "points": 0, "ok": False, "note": "unknown layer"}
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0
    failures: list[str] = []

    for region in viewport._USA_REGIONS:
        bbox = viewport._bbox_to_str(*region)
        try:
            points = await _fetch_region(layer, bbox)
        except Exception as exc:
            # ONE region short is the whole layer short: sweeping now
            # would delete every point that region was going to supply.
            failures.append(f"{bbox}: {str(exc)[:120]}")
            continue
        try:
            total += await db.upsert_poi_points(layer, points, stamp)
        except Exception as exc:
            failures.append(f"{bbox}: store failed: {str(exc)[:120]}")

    ok = not failures
    note = "" if ok else " | ".join(failures)
    try:
        await db.finish_poi_import(layer, stamp, total, ok, note)
    except Exception:
        logger.exception("poi import: could not close %s", layer)
    logger.info("poi import: %s — %d points, ok=%s%s",
                layer, total, ok, f" ({note})" if note else "")
    return {"layer": layer, "points": total, "ok": ok, "note": note}


async def import_all(db, stamp: str | None = None) -> list[dict]:
    """Every built-in layer, one after another with a pause between.

    Sequential on purpose: the mirrors are volunteer-run and a weekly
    job has no reason to arrive as six simultaneous region scans.
    """
    out: list[dict] = []
    dead = 0
    layers = list(POI_OVERPASS_QUERIES)
    for i, layer in enumerate(layers):
        if i:
            await asyncio.sleep(_BETWEEN_LAYERS_S)
        result = await import_layer(db, layer, stamp)
        out.append(result)
        # A layer that failed AND brought back nothing is the source
        # being unreachable, not this layer being awkward.
        dead = dead + 1 if (not result["ok"] and result["points"] == 0) else 0
        if dead >= _GIVE_UP_AFTER_DEAD_LAYERS:
            skipped = layers[i + 1:]
            logger.warning(
                "poi import: stopping — %d layers in a row got nothing from "
                "the source.  Not attempted: %s.  Nothing was changed; the "
                "next run picks up where this left off.",
                dead, ", ".join(skipped) or "none")
            for name in skipped:
                out.append({"layer": name, "points": 0, "ok": False,
                            "note": "not attempted — the source was not answering"})
            break
    done = sum(1 for r in out if r["ok"])
    logger.info("poi import: %d/%d layers refreshed", done, len(out))
    return out
