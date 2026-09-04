"""KPI — the account-wide performance analytics surface (docs/FEATURES.md).

Tier: shared.  One page for the whole account, delegatable to any role via
``can_view_kpi``.  Sections are DOMAINS, each fed by that feature's service
contract (never raw tables): Dispatchers (from ``features.loads.service``)
first; Fleet / Safety / Drivers compose their features' aggregates later.
Metrics are computed live at read time; the warehouse rollup tier is a
documented later optimization (add when loads history outgrows live SQL).
"""
