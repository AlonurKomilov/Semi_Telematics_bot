"""Scorecards — the Shared-tier feature surface.

The scoreboard READS (``router``) and the scoring-config endpoints
(``config_router``) live here, co-located with the frontend
``src/features/scorecards/``.  The scoring HUB machinery — engine,
orchestrator service, signals, and the rule/curve model — stays in
``capabilities/scorecards/`` (a hub aggregates scoring signals from features;
this feature CONSUMES it, the correct feature → hub direction).
"""
