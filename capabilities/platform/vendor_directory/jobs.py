"""Vendor-directory platform jobs.

The market-intel nightly job MOVED to its own family:
``capabilities/platform/market_intel/jobs.py`` (2026-07-18) — market
intelligence spans vendors AND parts, so it no longer lives under
either.  The launch gate is the ``market_intel_enabled()`` method on
the shared ``Database`` (console switch + env override); the old
env-only twin functions here and in features/vendors are gone.
"""
