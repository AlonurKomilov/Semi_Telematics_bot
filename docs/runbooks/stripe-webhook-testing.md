# Stripe webhook smoke test runbook

End-to-end verification of the billing webhook path using the Stripe CLI.
Drives every event_type the handler in `capabilities/platform/billing/stripe_client.py`
processes, checks the resulting DB state, and reads `/metrics` to confirm
the Prometheus counters fired.

This is a **manual** runbook — Stripe CLI integration needs a live API and
a forwarding tunnel that's painful to set up in CI but trivial to drive
in dev.

## Prerequisites

1. **Stripe CLI installed** — <https://stripe.com/docs/stripe-cli>
2. **`stripe login`** completed (one-time browser-based authorization)
3. **Test-mode account** — never run this against `sk_live_…` keys
4. **4truck API running locally** on port 8080 (default):

       make start-api

5. **Postgres reachable** via `$DATABASE_URL` (set in `.env`)
6. **`psql`** installed (the check phase queries the DB directly)

## One-time setup: webhook signing secret

The CLI's `listen` command generates an ephemeral `whsec_…` secret on
every restart.  Capture it once for the smoke session and trust it:

       ./scripts/stripe_smoke.sh tunnel

Output includes:

       > Ready! You are using Stripe API Version [...]. Your webhook signing secret is whsec_abc123xyz...

Copy that value into `.env` as `STRIPE_WEBHOOK_SECRET` and **restart the
API** so it picks up the secret:

       sudo make restart-api

The smoke script intentionally won't rotate the secret for you — running
arbitrary `.env` writes during smoke tests would be a footgun.

## Running the smoke

Three modes — choose based on whether you want to step through events
or just run the whole flow.

### Mode A — interactive (recommended, two terminals)

**Terminal 1** — tunnel:

       ./scripts/stripe_smoke.sh tunnel

**Terminal 2** — drive events for account #5:

       ./scripts/stripe_smoke.sh trigger 5
       ./scripts/stripe_smoke.sh check   5

The trigger phase fires, in order:

| Order | Event                          | Expected DB effect                                      |
|-------|--------------------------------|---------------------------------------------------------|
| 1     | `checkout.session.completed`   | `subscriptions.tier='starter'`, `status='active'`, item ids saved |
| 2     | `customer.subscription.updated`| `current_period_start` / `current_period_end` set       |
| 3     | `invoice.payment_failed`       | `status='past_due'`, `past_due_since` stamped, invoice row added (`status='open'`) |
| 4     | `invoice.payment_succeeded`    | `status='active'` (recovered), `past_due_since=NULL`, invoice row updated to `paid` |
| 5     | `customer.subscription.deleted`| `status='canceled'`, account tier → `free`              |

Every event also produces:
- A `processed_stripe_events` row (idempotency gate).
- A `billing_webhook_events_total{event_type, result="processed"}` Prometheus tick.
- For payment events: a Telegram notification to billing admins
  (`billing_notifications_total{kind, channel="telegram"}` ticks).

### Mode B — one-shot (single terminal)

       ./scripts/stripe_smoke.sh all 5

Runs `stripe listen` in the background, fires every event, runs `check`,
then kills the listener.  Use this for a quick sanity check; use Mode A
when you want to watch logs scroll.

### Mode C — idempotency replay

To prove the Day-1 idempotency gate works, re-run the trigger phase a
second time against the **same** event_ids — the script doesn't expose
event_id rotation but `stripe events resend evt_…` works.  Each replay
should produce `billing_webhook_events_total{result="duplicate"}` ticks
and **no** new `subscriptions` mutations.

## Checking metrics

Once the smoke completes:

       curl -s http://localhost:8080/metrics | grep '^billing_'

You should see at minimum:

       billing_webhook_events_total{event_type="checkout.session.completed",result="processed"} 1
       billing_webhook_events_total{event_type="invoice.payment_failed",result="processed"} 1
       billing_webhook_events_total{event_type="invoice.payment_succeeded",result="processed"} 1
       billing_notifications_total{channel="telegram",kind="payment_failed"} <N>
       billing_notifications_total{channel="telegram",kind="payment_recovered"} <N>

Where `<N>` is the count of billing-admin recipients for the account
under test.  Zero is suspicious — it means either (a) no Owner/Admin
users exist for the account, or (b) the per-account bot isn't registered.

## Common pitfalls

- **`SignatureVerificationError`** — `STRIPE_WEBHOOK_SECRET` doesn't match
  the value the listener printed.  Restart the API after updating `.env`.
- **404 on `/metrics`** — Prometheus deps aren't installed; the metric
  rows still fire but the endpoint returns nothing.  `pip install
  prometheus-client prometheus-fastapi-instrumentator` and restart.
- **`UndefinedColumnError`** — a billing schema migration didn't run.
  `make restart-api` re-triggers `run_all()`; check `api.log` for
  migration output.
- **Triggers don't carry `metadata.account_id`** — `stripe trigger` uses
  pre-built fixtures.  The `--add` flag in our script injects metadata
  onto the checkout session and subscription objects; events that
  derive from those (invoices) need the resolver fallback
  (`provider_customer_id` / `provider_subscription_id` lookup) which
  works once the checkout completes first — that's why event ordering
  matters in this smoke.

## Rollback

The smoke is non-destructive:  it only writes Stripe **test-mode** rows
plus their local mirrors.  To clean up:

       psql "$DATABASE_URL" <<'SQL'
       DELETE FROM billing_invoices            WHERE account_id = 5;
       DELETE FROM processed_stripe_events     WHERE account_id = 5;
       UPDATE subscriptions SET
           tier='free', status='active',
           provider_customer_id='', provider_subscription_id='',
           provider_base_item_id='', provider_extra_item_id='',
           past_due_since=NULL
       WHERE account_id = 5;
       SQL

Don't run that against production — `account_id = 5` may be a real
tenant.
