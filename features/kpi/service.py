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


def _validate_threshold_pairs(final: dict[str, float]) -> None:
    """Each metric's good/bad pair must point the right way — an inverted
    pair silently grades EVERY dispatcher wrong, so it is refused at
    write time with a message an admin can act on."""
    checks = [
        ("rpm_good", ">", "rpm_bad",
         "RPM: 'good at or above' must be higher than 'bad below'"),
        ("empty_pct_good", "<", "empty_pct_bad",
         "Empty miles: 'good at or below' must be lower than 'bad above'"),
        ("gross_per_truck_good", ">", "gross_per_truck_bad",
         "Gross per truck: 'good at or above' must be higher than 'bad below'"),
    ]
    for a, op, b, msg in checks:
        va, vb = final[a], final[b]
        ok = va > vb if op == ">" else va < vb
        if not ok:
            raise ValueError(f"{msg} (got {va:g} vs {vb:g})")


async def set_kpi_thresholds(
    db: Any, account_id: int, values: dict,
) -> dict[str, float]:
    """Persist overrides (known keys only, coerced to float).  Raises
    ``ValueError`` when the RESULTING thresholds (overrides merged over
    defaults) would invert a good/bad pair."""
    clean: dict[str, float] = {}
    for k, v in (values or {}).items():
        if k in DEFAULT_KPI_THRESHOLDS:
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
    _validate_threshold_pairs({**DEFAULT_KPI_THRESHOLDS, **clean})
    await db.set_account_setting(account_id, KPI_SETTING_KEY, json.dumps(clean))
    return await get_kpi_thresholds(db, account_id)
