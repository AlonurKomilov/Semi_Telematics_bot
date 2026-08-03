"""Data warehouses — analytical, tiered stores built + pruned by the
``data_lifecycle`` engines.

Each sub-package is one warehouse (its own tables, aggregation, and read
facade); the generic build/delete machinery lives in
``capabilities/data_lifecycle`` (rollups + retention), which every warehouse
registers with.  This umbrella holds no shared code yet — the aggregation is
always domain-specific — so it's just the home for the warehouses themselves.

  * ``telemetry`` — the vehicle telemetry warehouse (live → minute →
    hour → day → week tiers; health, faults, weather, efficiency facade).

A second analytical domain (e.g. scorecards) becomes a sibling sub-package and
reuses the same ``data_lifecycle`` engines.
"""
