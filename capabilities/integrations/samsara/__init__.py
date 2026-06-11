"""Samsara-specific integration routes.

The per-company key matrix, the account-wide + per-company history
backfill, the "Run all (30 days)" / "Refresh" surfaces, and the
aggregate health-status reconciler all live in ``router.py``.

These concepts have no Datatruck (or generic) analogue and are
intentionally namespaced under ``/integrations/samsara/...`` so the
URL surface is honest about which provider they apply to.

Generic routes that every provider gets (connect, disconnect,
toggles, test-connection, cadences) live in
``capabilities.integrations.shared.router`` and are not duplicated here.
"""

from __future__ import annotations
