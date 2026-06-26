"""Scorecard rules — the scoring rule MODEL (hub-internal).

The default rule/pillar definitions (``defs``), curve math (``curves``) and
exposure normalisation (``normalize``) that the scoring engine consumes.  This
is hub machinery; the per-tenant CONFIG surface that EDITS these rules lives
with the feature (``features/scorecards/config_router.py``), which
imports back into here (feature → hub).  Per the carve recipe this ``__init__``
re-exports NOTHING — import the submodule directly
(``from capabilities.scorecards.rules.defs import get_default_rules``).
"""
