"""KPI family — shared helpers.

Root of the family holds ORCHESTRATION AND SHARED PIECES ONLY: the
threshold config helpers that ``config.py`` serves.  Everything with a
section subject lives in that section's package — the dispatcher grades
and the incentive engine are in ``features/kpi/dispatch/``.
"""

from __future__ import annotations

import json
from typing import Any

KPI_SETTING_KEY = "kpi_thresholds"

# Defaults from the operating rules the platform ships with; every value is
# per-account overridable through /kpi/config.
DEFAULT_KPI_THRESHOLDS: dict[str, float] = {
    "rpm_good": 2.30,
    "rpm_bad": 2.00,
    "empty_pct_good": 10.0,
    "empty_pct_bad": 15.0,
    "gross_per_truck_good": 7500.0,
    "gross_per_truck_bad": 6500.0,
}


# ── Thresholds (account-configurable) ──────────────────────────────────


async def get_kpi_thresholds(db: Any, account_id: int) -> dict[str, float]:
    """The account's thresholds merged over the defaults; unknown / invalid
    override keys fall back."""
    merged = dict(DEFAULT_KPI_THRESHOLDS)
    try:
        raw = await db.get_account_setting(account_id, KPI_SETTING_KEY, "")
    except Exception:
        raw = ""
    if raw:
        try:
            override = json.loads(raw)
        except (TypeError, ValueError):
            override = None
        if isinstance(override, dict):
            for k, v in override.items():
                if k in merged:
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        pass
    return merged


async def set_kpi_thresholds(
    db: Any, account_id: int, values: dict,
) -> dict[str, float]:
    """Persist overrides (known keys only, coerced to float)."""
    clean: dict[str, float] = {}
    for k, v in (values or {}).items():
        if k in DEFAULT_KPI_THRESHOLDS:
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
    await db.set_account_setting(account_id, KPI_SETTING_KEY, json.dumps(clean))
    return await get_kpi_thresholds(db, account_id)
