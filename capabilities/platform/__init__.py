"""Platform sub-family — the DUAL-audience money domains.

A domain here has an operator face AND a customer face: billing is us
charging the customer's account, and the customer's own card page.  A
domain a customer can touch is not a system service, however much of it
faces the operator — the operator-ONLY services (security, capacity,
the directories, the watchdog) live one layer up, in ``system/``.

    features/                → the customer's working services — AUDIENCE: tenant
    capabilities/            → tenant-serving machinery — AUDIENCE: tenant
    capabilities/platform/   → dual-audience money domains — AUDIENCE: both
    system/                  → operator-only services — AUDIENCE: platform

Boundary rules (enforced by tests/test_layer_boundaries.py):
  * ``features/**`` never imports ``capabilities.platform.*``.
  * ``capabilities/platform/**`` never imports ``features.*``.
  * nothing below ``system/`` imports ``system``.

Current members: ``billing``.  The six packages that once sat beside it
remain here as one-release aliases to their ``system/`` homes.
SSOT: docs/FEATURES.md "Money domains" + "Backend patterns".
"""
