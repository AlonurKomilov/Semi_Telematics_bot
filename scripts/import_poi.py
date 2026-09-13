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
from features.live_map.poi.layers import POI_OVERPASS_QUERIES  # noqa: E402
from infra.platform import get_platform_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("poi.import")


async def show_status(db) -> int:
    counts = await db.count_poi_points()
    runs = await db.poi_layer_imports()
    width = max(len(name) for name in POI_OVERPASS_QUERIES)
    print(f"\n{'layer'.ljust(width)}  {'points':>7}  {'last good import':<22}  last run")
    print("-" * (width + 60))
    for layer in POI_OVERPASS_QUERIES:
        run = runs.get(layer) or {}
        when = run.get("imported_at") or "—  never"
        if run.get("ok") == 1:
            outcome = "ok"
        elif run:
            outcome = f"FAILED — {(run.get('note') or '')[:60]}"
        else:
            outcome = "—"
        print(f"{layer.ljust(width)}  {counts.get(layer, 0):>7}  {when:<22}  {outcome}")
    print()
    missing = [l for l in POI_OVERPASS_QUERIES if not counts.get(l)]
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
    elif args.all:
        print(f"Importing {len(POI_OVERPASS_QUERIES)} layers — this takes a while…")
        for r in await import_all(db):
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
    args = p.parse_args()
    if args.layer and args.all:
        p.error("--layer and --all are alternatives")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
