"""Storage capability — hybrid disk/cloud sync worker.

Lives in ``capabilities/`` rather than ``adapters/`` because it
orchestrates BOTH the local-disk adapter and the per-tenant cloud
adapter (Drive) plus the DB outbox.  The adapter layer stays free of
cross-tier policy.
"""
