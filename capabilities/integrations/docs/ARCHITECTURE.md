# Integrations — where a vendor stops

**The law:** vendor glue lives with the provider; provider-agnostic
ingest lives with the feature; and the sentence connecting them is the
resolver.

This was unwritten until 2026-09-14, and that is most of why it drifted:
the contract, the registry and the catalog were all built, and then
every consumer still said `"samsara"` out loud.

## The three layers, and what each may know

| Layer | Knows | Must never know |
|---|---|---|
| `adapters/telematics/<vendor>/` | the vendor's endpoints, spellings, units, quirks | anything about a feature |
| `adapters/telematics/protocol.py` | the canonical shapes and vocabularies every provider maps INTO | any one vendor |
| `capabilities/integrations/` | which provider serves which capability for which account | what a feature does with it |
| `features/<x>/` | the capability it needs and what to store | which vendor is behind it |

A feature that contains a vendor's name has a bug, not a shortcut. The
one documented exception today is `features/eld/ingest.py::_driver_links`,
which reads `users.samsara_driver_id` — a vendor-named column that
predates this rule and needs a general link table to retire.

## Adding a provider

1. `adapters/telematics/<vendor>/client.py` + `provider.py` implementing
   `TelematicsProvider`, and `register_provider` from its `__init__.py`.
2. Declare its `supported_capabilities`.
3. Flip its `PROVIDER_CATALOG` entry from `COMING_SOON` to `AVAILABLE`.

Nothing in `features/` changes. `features/eld/tests/test_eld_is_provider_agnostic.py`
proves that with a `FakeEldProvider` no feature code has ever heard of.

## Adding a capability

The order is load-bearing, and the reason is a rule the catalog learned
the hard way from the retired prune capability:

> **A declared capability renders a toggle. A toggle that controls
> nothing is worse than a missing feature.**

So the capability id, the provider's claim, the catalog default, the
`FeedSpec` and the ingest that fills the table all land **together** —
even though the id could technically ship earlier. `Capability.DRIVER_HOS`
was deliberately held back three commits for exactly this.

Two pinned lists will stop you if you forget one; answer them rather
than silencing them:

* `adapters/telematics/samsara/provider.py` asserts at IMPORT that the
  provider's capability set equals the catalog's. Drift fails the boot.
* `capabilities/data_lifecycle/tests/test_ingest_registry.py` pins every
  dataset's scheduler `job_id`, because the operator console and the
  scheduler snapshot key on those ids.

## The resolver

`capabilities/integrations/shared/resolver.py` answers *which provider
serves capability X for account A*, and `None` is a first-class answer
meaning **no connected provider offers this**.

It deliberately does NOT copy one rule from
`_for_each_account_with_capability`: that helper RUNS a job for an
account with no integration row at all, a rollout-era default that kept
existing pipelines alive. Here the question is "which provider", and
guessing one that was never connected is how a compliance surface ends
up answering from nothing.

It does share one: a toggle map written before a capability existed has
no opinion about it. Missing reads as enabled; only an explicit `False`
disables. Otherwise every newly-shipped feed starts dark on every
existing account.

When more than one connected provider qualifies, the first in **catalog
order** wins and the rest are logged — declaration order, so the winner
never depends on a vendor's name.

## Where a feature declares its ingest

`features/<x>/lifecycle.py`, via `IngestDataset` — the capability it
needs, its cadence, the tables it writes, its freshness SLA, and whether
silence is normal (`expect_rows`). That declaration is what puts the feed
under the watchdog. Add the module to `_CONTRIBUTORS` in
`capabilities/data_lifecycle/ingest/__init__.py` or it is never
discovered.

`expect_rows` is a real decision, not a default. Safety events are
sparse — a well-driven fleet reports none, and silence is good news.
Hours of service is the opposite: a connected ELD reports every driver on
every poll, so zero rows means the feed stopped, and a stalled HOS feed
looks exactly like a fleet that is entirely off duty.

## A tenant table's row-level security

Do NOT add the table's name to migration `057_enable_rls_tenant_tables`.
That migration is version-tracked and has already run on production, so a
name appended to its list protects only databases created after your
change — with no error, no failed boot, and tenant PII sitting
unprotected.

Put `ENABLE` / `FORCE ROW LEVEL SECURITY` + the policy in
`adapters/storage/platform_migrations.py`, which re-runs every boot.
Worked example: `migrate_eld_hos_live`.

## Known drift

`features/*` still reaches past the seam for a Samsara-shaped client:
about twenty modules import `infra.services.get_client` directly instead
of `get_telematics_client`, including `features/vehicles/service.py`,
`features/events/service.py` and `features/live_map/service.py`. That
sweep is its own arc and only becomes meaningful once there is a second
provider to resolve to.
