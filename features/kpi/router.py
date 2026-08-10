"""KPI family — the orchestrating router.

The root owns NO endpoints.  Each section (dispatch today; fleet, safety,
hr, drivers as they land) brings its own router, and this file only
aggregates them so ``interfaces/api/app.py`` keeps mounting one thing.
The family's config surface stays separate in ``config.py``
(``/kpi/config``), mounted config-first as everywhere else.
"""

from __future__ import annotations

from fastapi import APIRouter

from features.kpi.dispatch.router import router as _dispatch

router = APIRouter()
router.include_router(_dispatch)
