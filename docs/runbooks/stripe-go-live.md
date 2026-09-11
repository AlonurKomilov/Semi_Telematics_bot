# Stripe go-live — the checklist before `BILLING_PROVIDER=stripe`

Today production runs `BILLING_PROVIDER=stub`: prices are shown, nothing is
charged, no call reaches Stripe. Switching is one env change — but the
chain it turns on has pieces that live in Stripe and in `.env`, and a
missing piece fails silently (extras never billed) or on the customer's
first checkout. The Plans page (system console) shows this list live.

## 1. Stripe dashboard (test mode first, then live)

- **Keys**: `STRIPE_SECRET_KEY` (sk_test_… then sk_live_…). Price and
  Product ids are per mode — every id below must come from the same mode
  as the key.
- **Webhook endpoint**: `https://api.4truck.us/api/billing/webhook`, events
  `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`, `invoice.payment_succeeded`,
  `invoice.payment_failed`. Paste its signing secret into
  `STRIPE_WEBHOOK_SECRET`. The API refuses unsigned webhooks in stripe mode.
- **The per-extra-truck Price**: one recurring monthly USD Price (today
  $2.99, quantity-based) — create it by hand and put its id in
  `STRIPE_PRICE_EXTRA_VEHICLE`. Without it every subscription is single-line
  and extra trucks are never billed.
- **Customer Portal**: enable it (Billing → Customer portal) so "Manage
  payment" works. Plan switching is done in-app, not in the portal — leave
  the portal's "switch plan" off so there is one path. (The webhook does
  re-derive the plan from the Price a subscription lands on, so a portal
  switch would be followed — but only for Prices our plan rows know, and
  with none of the in-app path's checks.)

## 2. `.env`

`BILLING_PROVIDER=stripe`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PRICE_EXTRA_VEHICLE`, and the dashboard origin Stripe sends the
customer back to (`AUTH_BASE_URL` or `DASHBOARD_BASE_URL`, e.g.
`https://dash.4truck.us`; the default is that host). Restart `4truck-api`.

## 3. The plans (system console → Plans)

Save each priced plan once. In stripe mode a save with a price creates the
plan's Stripe Product (first time) and a Price for that amount, and writes
the ids on the row; the Stripe price id column is read-only from then on.
`STRIPE_PRICE_<TIER>` env vars are only a fallback for a row with no id.

## 4. Dry run in test mode — what to watch

1. A new account: Billing → Upgrade → Stripe Checkout shows the plan's
   label and price, plus the extras line; pay with `4242 4242 4242 4242`.
   Expect the redirect to `…/billing?success=1`, the webhook to set the
   tier, the Billing page to show the new plan, and the resolver to open
   the plan's features without a restart.
2. The same account: Upgrade to another plan → NO second checkout: the
   subscription's base item switches in place, prorated; the page reloads
   on the new plan. Check Stripe shows ONE subscription.
3. Change a price on the Plans page → a new Price appears in Stripe, the
   old one is archived; `subscribers_on_old_price` counts the existing
   subscribers; "Roll out price…" moves them with no proration — check a
   subscriber's next invoice in Stripe shows the new amount and nothing
   mid-cycle.
4. Cancel from the portal → `customer.subscription.deleted` → the account
   is on Free and the resolver closes the paid features.
5. `sync_billing_quantity` (the extras job) → the extras item's quantity
   equals the active-truck count.

## 5. Known limits, decided

- One extras Price for every plan (per-tier extras would need a
  `stripe_extra_price_id` column).
- Feature changes to a plan apply to its accounts at once (~2 minutes
  across workers), not at the period end.
- No tax, no coupons wired (comp accounts are a local overlay).
