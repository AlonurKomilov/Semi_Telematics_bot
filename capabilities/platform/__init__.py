"""Platform sub-family — SYSTEM-OWNER domains (AUDIENCE: platform).

Everything under ``capabilities/platform/`` serves 4truck the operator, not
the customer's daily work:

    capabilities/            → tenant-serving machinery (alerting, reporting,
                               ai, scorecards, warehouse, …) — AUDIENCE: tenant
    capabilities/platform/   → system-owner domains (billing, …)
                               — AUDIENCE: platform
    features/                → the customer's working services (payroll,
                               loads, drivers, …) — AUDIENCE: tenant

Boundary rules (enforced by tests/test_layer_boundaries.py):
  * ``features/**`` never imports ``capabilities.platform.*`` — the customer
    product must not depend on system-owner domains.
  * ``capabilities/platform/**`` never imports ``features.*`` — platform
    domains must not reach into the customer product.

Current members: ``billing`` (us charging the customer account — displayed
to customers as "Subscription").  Future members: the account-purge module
(when extracted from the scheduler), operator-console services.
SSOT: docs/FEATURES.md "Money domains" + "Backend patterns".
"""
