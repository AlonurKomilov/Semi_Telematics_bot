"""Capacity monitoring — the platform sub-family's resource telemetry.

Answers the operator's capacity-planning question ("how many more
vehicles/accounts fit on this server, and when do I upgrade?") from
measured data instead of guesses:

  * ``requests.py`` — per-request metering counters in Redis, the ONE
    store all gunicorn workers share (in-process counters are
    per-worker fragments — a real bug this module exists to bypass).
  * ``sampler.py``  — the 60s scheduler job: host CPU/RAM/disk/net via
    psutil + Postgres/Redis/queue/request-rate probes into
    ``system_metrics_minute``; folds finished hours into the hourly
    avg+peak tier and flushes per-account request counts.
  * ``retention.py`` — data-lifecycle hub contribution (platform-scope
    windows for all three tables).

System-owner domain: consumed by the operator console (system.4truck.us
Capacity page), never by customer surfaces — hence capabilities/platform/
(the layer guard keeps features/ from importing it).
"""

# Capacity-planning thresholds (fractions of a resource at PEAK) —
# SINGLE SOURCE for the router's headroom math, the console's bands,
# and the alert engine.  Below WATCH = comfortable; WATCH..ACT = plan
# the upgrade; ≥ ACT = upgrade now (and the alert fires).
WATCH_PCT = 70.0
ACT_PCT = 85.0
