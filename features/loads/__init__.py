"""Loads — the canonical load/shipment feature (docs/FEATURES.md).

Tier: shared.  Operators/dispatchers manage loads; drivers see their own.
OUR ``loads`` table is the single source of truth: manual entry is source #1,
a connected TMS (Datatruck) projects in as source #2.  KPI/AI consumers read
loads through ``features.loads.service`` (service-contract rule).
"""
