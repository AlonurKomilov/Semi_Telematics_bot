#!/usr/bin/env python3
"""Fill the map's built-in POI layers from OpenStreetMap, by hand.

The weekly job (``poi_import``, Sunday 03:40 UTC) does this on its own.
This script exists for the two times that is not enough: the FIRST run,
before a Sunday has come around, and the run after an outage when you
want to see it happen rather than read about it on Monday.

Usage
-----
    # What is stored right now, and when each layer last imported
    python3 scripts/import_poi.py

    # One layer (start here — it is the cheapest way to see it work)
    python3 scripts/import_poi.py --layer fuel_station

    # Every layer, sequentially.  Minutes, not seconds.
    python3 scripts/import_poi.py --all

What to expect
--------------
* SLOW ON PURPOSE.  Each layer asks three US-wide regions with a 200s
  patience and up to three attempts a minute apart — the public mirrors
  are queue-bound, and waiting is the thing a job can do that the map
  cannot.  A full run can take twenty minutes.
* A layer that could not be fetched CHANGES NOTHING.  It keeps its old
  points and its old date, and the failure is printed and stored.  A
  half-finished import must never leave a layer emptier than it found
  it.
* Safe to re-run, and safe to interrupt: points are keyed by their OSM
  identity, so a re-import updates rather than duplicates, and the sweep
  for what left OSM only happens when a layer finished cleanly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import asyncio
import logging
import os
import sys

# Run from anywhere: `python3 scripts/import_poi.py` puts scripts/ on the
# path, not the repo root, so `features` and `infra` are not importable
# without this.  Every script here that reaches into the app does the
# same — mine did not, and the first run said so.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from features.live_map.poi.importer import import_all, import_layer  # noqa: E402
from features.live_map.poi.overpass import close_http_session  # noqa: E402
from features.live_map.poi.layers import (  # noqa: E402
    POI_OVERPASS_QUERIES,
    POI_SPLITS,
    SERVED_LAYERS,
)
from infra.platform import get_platform_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("poi.import")


async def show_status(db) -> int:
    counts = await db.count_poi_points()
    runs = await db.poi_layer_imports()
    # SERVED and not fetched: `weigh_station` produces two layers, and
    # an operator reading this table wants to see both of them.
    width = max(len(name) for name in SERVED_LAYERS)
    # TWO DATES, because they answer different questions and an operator
    # reading one of them for the other is the bug this column was added
    # to end: "we ran" is always recent, "the data" can be a season old.
    print(f"\n{'layer'.ljust(width)}  {'points':>7}  {'we imported':<22}"
          f"  {'OSM extract':<22}  last run")
    print("-" * (width + 82))
    for layer in SERVED_LAYERS:
        run = runs.get(layer) or {}
        when = run.get("imported_at") or "—  never"
        # Unknown until a run records one — the layers imported before
        # the column existed have none, and none prints as none.
        base = run.get("osm_base") or "— unknown"
        if run.get("ok") == 1:
            outcome = "ok"
        elif run:
            outcome = f"FAILED — {(run.get('note') or '')[:60]}"
        else:
            outcome = "—"
        print(f"{layer.ljust(width)}  {counts.get(layer, 0):>7}  {when:<22}"
              f"  {base:<22}  {outcome}")
    print()
    missing = [l for l in SERVED_LAYERS if not counts.get(l)]
    if missing:
        print(f"Not loaded yet: {', '.join(missing)}")
        print("The map falls back to asking Overpass per request for those.\n")
    return 0


async def main_async(args: argparse.Namespace) -> int:
    await init_services()
    db = get_platform_db()
    if db is None:
        print("No platform database — check the environment.", file=sys.stderr)
        return 2
    if not hasattr(db, "upsert_poi_points"):
        print("PoiDirectoryMixin is not on Database yet — add it to "
              "adapters/storage/__init__.py first.", file=sys.stderr)
        return 2

    if args.layer:
        if args.layer not in POI_OVERPASS_QUERIES:
            print(f"Unknown layer {args.layer!r}.  Known: "
                  f"{', '.join(POI_OVERPASS_QUERIES)}", file=sys.stderr)
            return 2
        print(f"Importing {args.layer} — three US regions, minutes not seconds…")
        r = await import_layer(db, args.layer)
        print(f"  {r['layer']}: {r['points']} points, ok={r['ok']}"
              f"{'  — ' + r['note'] if r['note'] else ''}")
    elif args.reclassify:
        # RE-FILE WHAT WE ALREADY HAVE.  A split decided after an import
        # does not need the source asked again — every tag the classifier
        # reads is in the stored props.  Seconds, and the volunteer
        # mirrors are not touched at all.
        # A NEW VERSION, and the OLD extract date.  Clients compare
        # `imported_at` before spending a byte, so re-filing that kept the
        # old stamp moved 2,160 points and told nobody — the owner found a
        # CAT Scale still on the DOT layer against a table that no longer
        # held one.  `osm_base` is untouched: the points did not change,
        # so neither did their age.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for fetched, (produced, classify) in POI_SPLITS.items():
            when = await db.poi_layer_imported_at(fetched)
            base = await db.poi_layer_source_as_of(fetched)
            if not when:
                print(f"  {fetched}: never imported — nothing to re-file")
                continue
            counts = await db.reclassify_poi_points(produced, classify, stamp, base)
            print(f"  {fetched}: " + ", ".join(
                f"{n}={counts.get(n, 0)}" for n in produced)
                + f"   (version {when} → {stamp})")
    elif args.all or args.missing:
        todo = list(POI_OVERPASS_QUERIES)   # fetches, not served layers
        if args.missing:
            # A layer that already has a good import does not need one
            # today, and re-doing it spends clock the missing ones need.
            runs = await db.poi_layer_imports()
            todo = [l for l in todo if not (runs.get(l) or {}).get("imported_at")]
            if not todo:
                print("Every layer already has a good import — nothing to do.")
                await close_http_session()
                return await show_status(db)
        print(f"Importing {len(todo)} layer(s) — this takes a while…")
        for r in await import_all(db, only=todo):
            print(f"  {r['layer']}: {r['points']} points, ok={r['ok']}"
                  f"{'  — ' + r['note'] if r['note'] else ''}")
    # aiohttp shouts two ERROR lines about an unclosed session when a
    # script exits holding one, and an ERROR under a line reporting
    # success reads as a failure nobody can find.
    await close_http_session()
    return await show_status(db)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--layer", help="import ONE layer (see the status table for names)")
    p.add_argument("--all", action="store_true", help="import every built-in layer")
    p.add_argument("--missing", action="store_true",
                   help="import only the layers with no good import yet")
    p.add_argument("--reclassify", action="store_true",
                   help="re-file stored points under a split decided after "
                        "the import — no network, seconds")
    args = p.parse_args()
    if sum(bool(x) for x in (args.layer, args.all, args.missing,
                             args.reclassify)) > 1:
        p.error("--layer, --all, --missing and --reclassify are alternatives")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
