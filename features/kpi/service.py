"""KPI family — the orchestration root.

Root of the family holds ORCHESTRATION AND SHARED PIECES ONLY.
Everything with a section subject lives in that section's package:
the dispatcher grades, the incentive engine AND the grading thresholds
are all in ``features/kpi/dispatch/`` — RPM / empty-% / gross-per-truck
are dispatch metrics, so their bars are dispatch config.

This module re-exports the dispatch thresholds under their historical
names because the root config router (``features/kpi/config.py``)
serves them at ``/kpi/config`` — the root may import sections (that IS
orchestration); sections may never import each other
(``features/kpi/tests/test_kpi_section_boundaries.py`` enforces it).
"""

from __future__ import annotations

from features.kpi.dispatch.thresholds import (  # noqa: F401
    DEFAULT_KPI_THRESHOLDS,
    KPI_SETTING_KEY,
    get_kpi_thresholds,
    set_kpi_thresholds,
)
