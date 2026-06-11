"""Product features — tier-agnostic domain packages.

Every user-facing feature (entity, workflow, or standalone) lives here;
``capabilities/`` holds only the four aggregator hubs (alerting, reporting,
ai, scoring) and platform substrate.  See docs/FEATURES.md for the taxonomy
and the dependency rule (hubs → features → platform).
"""
