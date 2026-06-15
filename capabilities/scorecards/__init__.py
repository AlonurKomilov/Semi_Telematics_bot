"""Driver Scorecard scoring engine.

Feature-driven composite scoring: each existing user-facing feature
(Safety Events, Maintenance, Driver Efficiency, …) exposes a *signal*
module that emits a uniform dict of facts about a driver over a window.
The engine evaluates a configurable set of rules against those signals
and produces a 0-100 composite score plus a bonus / penalty breakdown.

Adding a new feature later = drop a new file under ``signals/`` and add
a few rule rows.  No engine change.
"""
