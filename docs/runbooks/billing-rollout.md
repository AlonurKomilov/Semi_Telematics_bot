# Billing rollout runbook

Production cutover of the redesigned billing pipeline (Days 1–7) — Stripe
webhook idempotency, invoice persistence, enforcement middleware, comp
accounts, active-vehicle pricing, two-line Stripe subscriptions,
notifications, and metrics.

This runbook walks the deploy in **soft-launch order**: schema-first, then
backend gated dark, then frontend, then the per-customer rollout gates
(comped pilot → starter cohort → full).  Every gate can be held without
touching the rest of the system.

## Pre-flight

1. **Run the env checker** from the project root:

       ./scripts/billing_preflight.sh

   Exit code 1 = stop, fix, re-run.  Exit code 0 with warnings = proceed
   only if every warning is intentional (e.g. STRIPE_PRICE_PRO empty
   because you haven't launched the Pro tier yet).

2. **Read the test signal**:

       python3 -m pytest capabilities/platform/billing/tests/test_phase_c_e.py -q --no-header

   Expect 82/82 pass.  Any failure here is a blocker.

3. **Confirm the dashboard build is fresh**:

       make dashboard-build
       grep -q "Complimentary Account" interfaces/dashboard/dist/assets/*.js

   The grep matches one of the Day-8 strings; missing it means the
   Billing UI shipped is the old one (active vehicles / line items
   won't render).

4. **Stripe Dashboard sanity**:
   - Webhook endpoint configured for `https://api.4truck.us/billing/webhook`.
   - Events subscribed: `checkout.session.completed`,
     `customer.subscription.updated`, `customer.subscription.deleted`,
     `invoice.payment_succeeded`, `invoice.payment_failed`.
   - Signing secret in `.env` matches the one Stripe shows.
   - Prices visible: Starter $49, Pro $99, Extra Vehicle $2.99
     (all set to "Quantity is variable" for the Extra one).

## Deploy

The schema migrations are idempotent and additive — they run inside
`Database.initialize()` on API boot, so a plain `make restart-api` is
the whole deploy step.  Order:

       sudo ./scripts/billing_preflight.sh   # final sanity in prod env
       sudo make restart-api                 # picks up migrations + new code
       sudo make restart-bot                 # picks up scheduler jobs (monthly + comp sweep)

**Watch the API log** for migration completion:

       tail -F api.log | grep -iE 'migration|billing'

You should see (or "already applied — skipping" for re-runs):

       processed_stripe_events table created/verified
       billing_invoices table created/verified
       subscriptions comp columns added/verified
       comp_account_history table created/verified
       subscriptions provider_*_item_id columns added/verified
       billing_usage_snapshots active/inactive columns added/verified
       idx_vehicle_state_active_billing created (migration 059)

If you see `column "X" does not exist`, an index was created against a
column that wasn't added yet — that's the Day-3 trap; the fix is in
[adapters/storage/platform_schema.py:254-265](../../adapters/storage/platform_schema.py#L254-L265),
double-check it's deployed.

## Soft-launch gates

All three gates default to OFF.  Flip them in order; hold each gate for
at least 24 h to surface scheduler-tick bugs (the monthly snapshot and
comp sweep both fire daily/monthly so a single bad cycle is the
diagnostic signal).

### Gate 1 — Comped pilot (no real charges)

1. Keep `BILLING_PROVIDER=stub` for now (or `BILLING_ENFORCEMENT_ENABLED=0`
   if you've already switched to stripe).
2. Pick one friendly account.  Grant a comp via the API:

       curl -X POST https://api.4truck.us/billing/comp/grant \
         -H "Authorization: Bearer $SYSTEM_OWNER_JWT" \
         -H "Content-Type: application/json" \
         -d '{
           "account_id": 5,
           "expires_at": "2099-12-31T00:00:00+00:00",
           "reason": "soft launch pilot"
         }'

3. Confirm the recipient sees the **Complimentary Account banner** in
   the dashboard and a Telegram notification arrives.
4. Verify `compute_billing` returns the right active-vehicle count by
   loading `/billing/summary` and comparing **Active Vehicles** vs
   the truck list in Fleet → Vehicles.
5. Hold for 24 h.  Check the daily 03:00 UTC `billing_comp_expiry_sweep`
   logs — should report `0 expired, 0 reminded, N checked`.

### Gate 2 — First paid Starter (low-stakes Stripe)

1. Flip `BILLING_PROVIDER=stripe` in `.env`, restart the API.
2. Run preflight again — it should now require `STRIPE_SECRET_KEY`,
   `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER`, and
   `STRIPE_PRICE_EXTRA_VEHICLE`.
3. Pick a real but small fleet (≤5 trucks, well-known operator) and
   walk them through checkout.  Use Stripe **test mode** the first
   time:  `STRIPE_SECRET_KEY=sk_test_…` + test card `4242 4242 4242 4242`.
4. Confirm the webhook flow end-to-end with the smoke script:

       ./scripts/stripe_smoke.sh check 7   # account_id 7

   The `subscriptions` row should show `provider_base_item_id` and
   `provider_extra_item_id` populated.  `/metrics` should show
   `billing_webhook_events_total{event_type="checkout.session.completed",result="processed"}` ticked.
5. Hold for 24 h.  At least one Samsara ingest cycle (every 60s) will
   run `sync_billing_quantity` against the new Stripe customer; check
   `/metrics` for `billing_sync_quantity_total{result="noop"}` increments.
6. Switch to live mode (`sk_live_…`) once you're comfortable.

### Gate 3 — Enforcement on

1. Set `BILLING_ENFORCEMENT_ENABLED=1` in `.env`, restart the API.
2. From this point a `canceled` or `unpaid` subscription gets HTTP 402
   on every non-billing API call.  Comp accounts and `past_due_since +
   BILLING_GRACE_PERIOD_DAYS` (default 7) still pass through.
3. Trigger the failure path against your test account:

       stripe trigger invoice.payment_failed

4. Wait `BILLING_GRACE_PERIOD_DAYS` (or temporarily set it to `0` for
   smoke), then confirm `/api/user/me` returns 402 from a dashboard
   tab logged in as the past-due account.  The **PastDueBanner** should
   render in the Billing page.

## Monitoring

Once live, dashboards (or `curl /metrics | grep '^billing_'`) should
show:

| Metric                                       | What it tells you                              |
|----------------------------------------------|------------------------------------------------|
| `billing_webhook_events_total`               | Stripe webhook health by event_type + outcome  |
| `billing_sync_quantity_total{result="patched"}` | Active-vehicle count changed → Stripe was updated |
| `billing_sync_quantity_total{result="stripe_error"}` | Stripe API failed during a sync — investigate  |
| `billing_notifications_total`                | Per-kind recipient reach (low number = wrong admin lookup) |
| `billing_comp_sweep_total{action="expired"}` | Comp windows that lapsed in the last day       |

**Alert on**:
- `rate(billing_webhook_events_total{result="error"}[5m]) > 0` — handler
  blew up on a real event.
- `rate(billing_webhook_events_total{result="invalid_signature"}[5m]) > 1` —
  webhook secret drift or someone hitting the endpoint without it.
- `rate(billing_sync_quantity_total{result="stripe_error"}[15m]) > 0` —
  fleet counts no longer landing on Stripe; bills will be wrong if it
  persists across the billing-period close.

## Rollback

The riskiest step is `BILLING_PROVIDER=stripe` because once a real
Stripe subscription exists, deleting it has financial consequences.
Each gate has its own rollback:

### From Gate 3 → Gate 2

       # Stop blocking past-due accounts
       sed -i 's/^BILLING_ENFORCEMENT_ENABLED=.*/BILLING_ENFORCEMENT_ENABLED=0/' .env
       sudo make restart-api

### From Gate 2 → Gate 1

       # Stop creating new Stripe subscriptions; existing ones keep billing
       sed -i 's/^BILLING_PROVIDER=.*/BILLING_PROVIDER=stub/' .env
       sudo make restart-api

   Existing Stripe customers can still be managed via the Stripe Dashboard.
   Cancel via Stripe directly — the `customer.subscription.deleted` webhook
   handler (still wired) will flip our subscription row to `canceled`.

### From Gate 1 → no comps

       # Revoke the pilot comp
       curl -X POST https://api.4truck.us/billing/comp/revoke \
         -H "Authorization: Bearer $SYSTEM_OWNER_JWT" \
         -H "Content-Type: application/json" \
         -d '{"account_id": 5, "reason": "soft launch rollback"}'

   Schema rollback is intentionally NOT documented — additive columns
   never need to be dropped, and `comp_account_history` is auditable
   data finance may need months later.

## Known incidents to watch for

- **`UndefinedColumnError: is_comped`** during boot — schema literal
  referenced the new column before the migration could ALTER TABLE
  it.  Fixed in Day-3 followup; the index now lives in the migration
  only.
- **All `/api/*` returning 502** — gunicorn workers crash-looping on
  the above.  `api.log` shows the traceback.  `make restart-api`
  after pulling the fix.
- **`STRIPE_WEBHOOK_SECRET` blank** — `_stripe()` factory fail-fast.
  API refuses to boot in stripe mode; preflight catches it before deploy.
- **Vehicles billed at full Samsara count** — `STRIPE_PRICE_EXTRA_VEHICLE`
  unset, so checkout silently falls back to single-line.  Preflight
  warns about this.

## Cleanup

After the soft launch is complete (~2 weeks):

- Remove the `BILLING_PROVIDER=stub` fallback from `.env` files in
  production environments (keep stub for local dev / CI).
- Delete the pilot comp if it was issued as "free trial" rather than
  long-term complimentary.  Don't delete the `comp_account_history`
  row — it's the audit record.
- Lower `BILLING_GRACE_PERIOD_DAYS` from the soft-launch default if
  you raised it during Gate 3 testing.
