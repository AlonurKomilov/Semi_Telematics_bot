"""The scheduled half of serving POI layers from our own table.

Weekly, because the data is weekly-ish at best: a truck stop reaches
OpenStreetMap days after it opens and does not move afterwards.  Asking
more often would spend a volunteer mirror's time to learn nothing.

Sunday 03:40 UTC — inside the European night, which is where most of the
public Overpass instances are and when their queues are shortest.  The
run takes minutes and answers to nobody, so a quiet hour costs us
nothing and costs them least.
"""

from __future__ import annotations

import logging

from .importer import import_all

logger = logging.getLogger(__name__)


async def job_poi_import(_app=None) -> None:
    """Refresh every built-in POI layer from OSM.

    Never raises: a week without a refresh leaves the previous week's
    points on the map, which is the whole point of storing them.  A dead
    scheduler would be a worse outcome than stale truck stops.
    """
    from infra.platform import get_platform_db

    try:
        db = get_platform_db()
    except Exception:
        logger.exception("poi import: no platform database")
        return
    if db is None:
        return
    try:
        await import_all(db)
    except Exception:
        logger.exception("poi import: run failed")
