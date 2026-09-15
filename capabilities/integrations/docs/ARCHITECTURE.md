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
2. Declare its `supported_capabilities` **and** its
   `hos_clocks_reported` (see below). Call
   `assert_declarations_agree(YourProvider)` at module scope — the
   shared guard in `catalog.py`, so the invariant is written once.
3. Add the entry to `PROVIDER_CATALOG`, or flip an existing
   `COMING_SOON` one to `AVAILABLE`.
4. Add a construction branch to `infra/services.py::get_telematics_client`.
5. One line in `adapters/telematics/__init__.py` so importing the
   umbrella registers it.

`features/eld/tests/test_eld_is_provider_agnostic.py` proves the ingest
never learns a vendor's name, with a `FakeEldProvider` no feature code
has heard of.

### What the second ELD actually cost — read this before the third

ORIENT ELD landed 2026-09-15 and is the first provider added after that
claim was made. The claim held exactly where it was written:
`features/eld/ingest.py` did not change by one line, and neither did
the store, the router, or the scope rules.

It did NOT hold for `features/eld/service.py` and
`features/eld/ai_tool.py`, and the reason is worth more than the rule:

> ORIENT ELD reports duty status and **none of the four countdowns**.
> That is not a broken integration — its public API has seven endpoints
> and not one carries remaining drive, shift, cycle or break time.

Nothing in the contract could express that. `HosSnapshot` already said
`None` meant "the provider did not report this clock", but only
per-row, after a fetch — so a page could not tell "this device never
reports it" from "nobody has driven yet today", and rendered four empty
columns either way. On a compliance page four dashes read as four
zeroes, and a header that never warns reads as "nobody is near a
limit".

So the vendor-neutrality was intact; the **vocabulary was incomplete**.
The fix is `HosClock` in `protocol.py` plus a
`hos_clocks_reported: frozenset[str]` every provider declares:

* declared, never derived — deriving it from ingested rows conflates a
  vendor that never reports a clock with a driver who has not started
  their day, and an adapter mapping bug that yields `None` would HIDE
  the broken column instead of showing it;
* `frozenset()` is a legal answer, and the surfaces say it in words
  ("ORIENT ELD reports duty status only") rather than in blanks;
* `adapters/telematics/tests/test_hos_clock_declarations.py` feeds every
  registered HOS provider its own most-generous fixture and fails if
  the declaration and the payload disagree **in either direction**. A
  new ELD with no fixture there fails by name.

The lesson for the third provider: the question to ask is not only
"does the feature name a vendor" but "can the contract express what
this vendor cannot do".

## Two providers offering the same capability

The resolver picks the **first in catalog order** and logs the rest.
Against the shipped catalog that means **Samsara wins hours of service
over ORIENT ELD**, because Samsara is declared first.

On an account that runs one vendor for telematics and a different one
as its actual ELD, that is the wrong answer, and it fails quietly: the
job succeeds, the page fills from the wrong source, and only a log line
says a second ELD was skipped.

The escape is the per-capability toggle the resolver already honours —
switch OFF the telematics provider's "Hours of service" on its
Integration card and the ELD takes over. That is an operator action,
not a code path. `features/eld/tests/test_two_elds_on_one_account.py`
pins it against the REAL catalog, because a fixture catalog would pass
forever while the shipped one said something else.

## Per-company API keys

Some vendors issue a key per COMPANY while the platform schedules per
ACCOUNT (Samsara, ORIENT ELD). Datatruck does not. The dashboard has
always called that surface through a `{providerId}` template; only the
backend hardcoded `/integrations/samsara/...`, which the second
per-company provider discovered by getting a 404 on a card that had
rendered perfectly.

`capabilities/integrations/shared/companies_router.py` is that surface
with the vendor name removed. **It is mounted LAST**, after every
vendor router, so literal `/samsara/...` paths still win their own
handlers — Samsara's carry a dual-write to the legacy
`companies.samsara_api_key` column that no other provider has, and
shadowing them would drop that write with nothing raised anywhere.
`capabilities/integrations/tests/test_per_company_routes_are_generic.py`
pins the precedence by the NAME of the winning handler, which is the
only thing that changes when the include order does.

One divergence to know: Samsara's `build_multi_company_client` lends a
single account-level token to any company with no key of its own.
ORIENT's builder deliberately does **not** — its key is scoped to one
company, so lending it does not fail, it succeeds and returns the wrong
company's drivers.

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
