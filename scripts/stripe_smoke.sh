#!/usr/bin/env bash
# stripe_smoke.sh — manual Stripe CLI end-to-end smoke for the
# 4truck billing webhook.  Walks through every event_type the
# handler in capabilities/billing/stripe_client.py processes and
# checks the resulting state in Postgres.
#
# Prerequisites (one-time):
#   - Stripe CLI installed:  https://stripe.com/docs/stripe-cli
#   - ``stripe login`` completed
#   - 4truck API running locally:   make start-api  (or ``make restart-api``)
#   - PostgreSQL reachable via $DATABASE_URL
#   - STRIPE_WEBHOOK_SECRET set to the value ``stripe listen`` prints
#     (the script reminds you on first run)
#
# Usage:
#   ./scripts/stripe_smoke.sh tunnel        # leaves ``stripe listen`` running
#   ./scripts/stripe_smoke.sh trigger ACCT  # fires every event with metadata.account_id=ACCT
#   ./scripts/stripe_smoke.sh check  ACCT   # prints the DB rows the trigger should have produced
#   ./scripts/stripe_smoke.sh all   ACCT    # tunnel → trigger → check (one terminal per phase)
#
# This is intentionally a shell script, not a pytest suite — Stripe CLI
# integration needs a live API + listen tunnel that's painful to set up
# in CI but trivial to drive interactively in dev.

set -euo pipefail

API_URL="${API_URL:-http://localhost:8080}"
WEBHOOK_PATH="${WEBHOOK_PATH:-/api/billing/webhook}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL must be set}"

# Stripe events the 4truck webhook handler processes.  Order matters:
# checkout completion must come first so the subscription row has the
# ids the later invoice events look up by.
EVENTS=(
    checkout.session.completed
    customer.subscription.updated
    invoice.payment_failed
    invoice.payment_succeeded
    customer.subscription.deleted
)

cmd_tunnel() {
    echo "→ Starting Stripe CLI listener on $API_URL$WEBHOOK_PATH"
    echo "  Copy the printed 'whsec_…' value into your .env as STRIPE_WEBHOOK_SECRET"
    echo "  and restart the API before running trigger / all."
    echo
    exec stripe listen --forward-to "$API_URL$WEBHOOK_PATH"
}

cmd_trigger() {
    local acct="${1:?usage: $0 trigger ACCOUNT_ID}"
    for evt in "${EVENTS[@]}"; do
        echo "→ stripe trigger $evt  (account_id=$acct)"
        # ``--add metadata[account_id]=N`` ensures our resolver finds the
        # right account; without it the handler would have to fall back
        # to customer/subscription lookup which depends on the order
        # ``stripe trigger`` creates objects in.
        stripe trigger "$evt" \
            --add "checkout_session:metadata.account_id=$acct" \
            --add "subscription:metadata.account_id=$acct" \
            --add "checkout_session:metadata.tier=starter" \
            || echo "  ⚠ trigger $evt returned non-zero (some events have no metadata override)"
        sleep 2  # let the webhook handler commit before the next event
    done
    echo
    echo "✅ All triggers fired.  Run ``$0 check $acct`` to inspect the DB."
}

cmd_check() {
    local acct="${1:?usage: $0 check ACCOUNT_ID}"
    echo "→ Subscription row for account_id=$acct"
    psql "$DATABASE_URL" -c "
        SELECT account_id, tier, status, provider_customer_id,
               provider_subscription_id,
               provider_base_item_id, provider_extra_item_id,
               past_due_since
        FROM subscriptions
        WHERE account_id = $acct;
    "
    echo
    echo "→ Recent processed Stripe events"
    psql "$DATABASE_URL" -c "
        SELECT event_id, event_type, processed_at
        FROM processed_stripe_events
        WHERE account_id = $acct
        ORDER BY processed_at DESC
        LIMIT 10;
    "
    echo
    echo "→ Recent invoices"
    psql "$DATABASE_URL" -c "
        SELECT provider_invoice_id, status, amount_due_cents, amount_paid_cents,
               hosted_invoice_url
        FROM billing_invoices
        WHERE account_id = $acct
        ORDER BY created_at DESC
        LIMIT 10;
    "
    echo
    echo "→ Prometheus metrics — billing_webhook_events_total"
    if curl -fsS "$API_URL/metrics" >/dev/null 2>&1; then
        curl -fsS "$API_URL/metrics" | grep -E "^billing_webhook_events_total|^billing_sync_quantity_total|^billing_notifications_total" || echo "  (no rows yet — make sure /metrics is exposed)"
    else
        echo "  (skipping — $API_URL/metrics not reachable)"
    fi
}

cmd_all() {
    local acct="${1:?usage: $0 all ACCOUNT_ID}"
    echo "  Running tunnel + trigger + check in sequence."
    echo "  (For longer sessions, run each command in its own terminal.)"
    echo
    stripe listen --forward-to "$API_URL$WEBHOOK_PATH" &
    local listen_pid=$!
    sleep 3  # give listen time to register
    trap "kill $listen_pid 2>/dev/null || true" EXIT
    cmd_trigger "$acct"
    sleep 3  # let the last webhook commit
    cmd_check "$acct"
}

case "${1:-}" in
    tunnel)  cmd_tunnel ;;
    trigger) shift; cmd_trigger "${1:-}" ;;
    check)   shift; cmd_check "${1:-}" ;;
    all)     shift; cmd_all "${1:-}" ;;
    *)
        cat <<EOF
Usage: $0 <tunnel|trigger|check|all> [ACCOUNT_ID]

  tunnel             Run ``stripe listen`` forwarding to $API_URL$WEBHOOK_PATH
  trigger ACCOUNT    Fire every webhook event with metadata.account_id=ACCOUNT
  check   ACCOUNT    Print the DB rows the triggers should have produced
  all     ACCOUNT    tunnel → trigger → check in sequence (single terminal)

See docs/runbooks/stripe-webhook-testing.md for full prerequisites.
EOF
        exit 1
        ;;
esac
