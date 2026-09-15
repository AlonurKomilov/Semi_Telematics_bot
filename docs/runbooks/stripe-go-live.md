# Stripe go-live — the checklist before `BILLING_PROVIDER=stripe`

Today production runs `BILLING_PROVIDER=stub`: prices are shown, nothing is
charged, no call reaches Stripe. Switching is one env change — but the
chain it turns on has pieces that live in Stripe and in `.env`, and a
missing piece fails silently (extras never billed) or on the customer's
first checkout. The **Payment wiring** card at the top of the Plans page
(system console) asks Stripe itself and shows what is still missing —
including the two an environment variable cannot see: a Price id from
the other Stripe mode, and a Product id pasted where a Price id belongs.

## 1. Stripe dashboard (test mode first, then live)

- **Keys**: `STRIPE_SECRET_KEY` (sk_test_… then sk_live_…). Price and
  Product ids are per mode — every id below must come from the same mode
  as the key.
- **Webhook endpoint**: `https://api.4truck.us/billing/stripe/webhook` (the
  provider-named path; `/billing/webhook` is an alias. The api host mounts
  routes without the `/api` prefix; on dash.4truck.us and 4truck.us the same
  route is under `/api/` — probed 2026-09-11), events
  `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`, `invoice.payment_succeeded`,
  `invoice.payment_failed`, `customer.updated` (that last one is how a
  billing address the customer changes in Stripe's own portal gets back
  to us — without it our copy drifts and receipts keep going to the old
  address). Paste its signing secret into
  `STRIPE_WEBHOOK_SECRET`. The API refuses unsigned webhooks in stripe mode.
- **The per-extra-truck Price**: nothing to make by hand. Each plan's
  extras Price is created on Save beside its base Price, on the same
  Product, from the plan's "Extra truck ($/month)" — a plan that bills
  no extra truck sends no extras line. (`STRIPE_PRICE_EXTRA_VEHICLE` is
  legacy: one env-wide Price used to serve every plan, so a plan whose
  row said $4.99 was billed at that Price's amount. Keep it set only
  while subscriptions made under it exist — it lets the rollout
  recognise their extras item and move it.)
- **Customer Portal**: enable it (Billing → Customer portal) so "Manage
  payment" works. Plan switching is done in-app, not in the portal — leave
  the portal's "switch plan" off so there is one path. (The webhook does
  re-derive the plan from the Price a subscription lands on, so a portal
  switch would be followed — but only for Prices our plan rows know, and
  with none of the in-app path's checks.)

## 2. `.env`

`BILLING_PROVIDER=stripe`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
and `DASHBOARD_BASE_URL` — the host that SERVES the dashboard,
because that is where Stripe returns the customer. The apex answers 404
for `/billing`, so an `AUTH_BASE_URL` pointing there used to hand a
paying customer an error page; `DASHBOARD_BASE_URL` now wins over it.
Restart `4truck-api`, then read the Payment wiring card.

## 3. The plans (system console → Plans)

Save each priced plan once. In stripe mode a save with a price creates the
plan's Stripe Product (first time) and a Price for that amount, and writes
the ids on the row; the Stripe price id column is read-only from then on.
A plan that is priced but has no Stripe price yet shows **Create Stripe
price** instead of "No changes", so this step needs no pretend edit.
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
5. `sync_billing_quantity` → the extras item's quantity equals the
   account's non-archived trucks minus the plan's included trucks (the
   Vehicles list, not Samsara: add a truck by hand, archive one, and
   press "Sync quantity" on the account's console page each time —
   the quantity follows; the daily `billing_quantity_sync` job does the
   same for every account at 03:30 UTC). After the first sync the
   subscription row carries `billed_quantity`; change the quantity in
   the Stripe dashboard by hand and sync again — expect a `drift`
   warning in the API log and the quantity back to the registry's.

## 4b. Switching from the sandbox to live — the ids do not carry over

Product, Price, Customer and Subscription ids are per mode. After the
dry run the `plans` rows remember SANDBOX Product/Price ids, and a Save
with an unchanged price is a no-op — so on live keys checkout would send
a sandbox Price and Stripe would refuse it. Before swapping the keys:

1. Do the dry run on a TEST account, never the real one. While
   `BILLING_PROVIDER=stripe` with `sk_test_…`, every customer's Billing
   page offers a test checkout — keep that window short.
2. Run this once (the operator runs it; it touches only the rows the dry
   run created):

   ```sql
   -- plans: forget the sandbox Product/Price ids so the next Save
   -- creates live ones (a Save creates a Price when the row has none)
   UPDATE plans SET stripe_price_id = '', stripe_product_id = '', stripe_extra_price_id = '';
   -- dry-run subscriptions: nothing in live Stripe knows these ids
   UPDATE subscriptions
      SET provider = 'stub', provider_customer_id = '',
          provider_subscription_id = '', provider_base_item_id = '',
          provider_extra_item_id = '', provider_base_price_id = '',
          billed_quantity = NULL, billed_at = NULL
    WHERE provider = 'stripe';
   ```

   The dry-run account keeps the tier the test checkout gave it; move it
   back from its console page (allowed now that Stripe no longer bills
   it). Rollout history rows from the dry run are just history.
3. In the live account: the API key (`sk_live_…`), a NEW webhook
   endpoint (its own `whsec_…`), the Customer Portal switched on for
   live too. No Price to make by hand — step 5 makes them.
4. `.env` → the live values; restart `4truck-api` (the plan cache is
   rebuilt at boot).
5. Plans page → Save each priced plan once more ("Create Stripe price"
   is lit on each): the row has no ids, so the Save creates the live
   Product, the base Price, and a Product and Price for the extra truck
   — its own Product because Stripe names a checkout line and an
   invoice line after the Product, and one shared between them printed
   the plan's name twice. Until a plan is saved,
   its checkout refuses rather than bill at another plan's amount, and
   the wiring card says which plan is waiting.
6. One real checkout on your own account with a real card, then refund
   it from the Stripe dashboard — that is the live proof.

## 4c. The plan nobody can buy

A plan offered at **no price** is a "talk to us" plan — Enterprise is
the case this exists for. Its card does not open a checkout: it opens a
short form, records the request with a case number the customer can
quote (`4T-202609-0007`), pings the operators on Telegram, and emails
`SALES_EMAIL` when that is set. Requests land in the console under
**Plan requests**, with the open count badged in the sidebar.

Set `SALES_EMAIL` in `.env` if you want the email copy; without it the
Telegram notice still goes out, because that channel needs nothing
beyond `SYSTEM_OWNER_IDS`. One open request per account and plan —
pressing the button twice joins the one already made. Closing a request
lets that customer ask again later.

## 4d. A plan for one customer

When a Contact-Sales conversation ends in terms nobody else gets — a
flat monthly figure, their own truck allowance — the plan is an
ordinary row made in the same editor as every other: **Plans → New
plan** — it lands in the **Offers** tab, hidden — set the price, the
included trucks, the per-truck extra and the features, and **leave it
there** (the Public tab is every customer's page). Under Stripe, press
**Create Stripe price** on it; a private plan needs a Price of its own
because the webhook reads the tier back from the Price.

Then open it to that one account: from the case on **Plan requests**
("Offer a plan…", which also marks the case contacted and emails the
address on it), or from the plan's column in **Plans → Offers** ("Offer
to an account…"). The plan appears on that account's Billing page as
"Prepared for your account"; every other account still cannot see it or
buy it by name. Nothing else moves — the customer pays for it there by
pressing Upgrade, and from then on the subscription row is the truth.
The × beside "only for …" withdraws the offer; a subscription already
made on the plan is untouched (`capabilities/platform/billing/offers.py`).

## 4e. `.env.test` — where the TEST side's keys live

`.env.test.example` (committed) lists every value the test side of the
platform needs — Stripe's sandbox key and webhook secret, test bots, a
test database, test hosts — with the word TEST on it so a value copied
into `.env` by mistake is recognisable at a glance. Copy it to
`.env.test` (gitignored) and fill it. Nothing loads that file on its
own: `make restart` reads `.env` and only `.env`. Its two uses:

- `make preflight-test` — the billing preflight, read-only, against the
  test file: is the sandbox configuration whole?
- the configuration a STAGING server would run from — its own database
  (the live `plans` rows hold live Price ids a test key cannot use) and
  its own hosts. Without a staging server, a real test after go-live is
  section 4b's step 6: a real card, then a refund.

## 4f. The receipt a paying customer gets

Two senders are possible and only one should end up on. Stripe's own
receipt is a dashboard switch (**Settings → Customer emails →
Successful payments**) — no code, Stripe's infrastructure, a link to a
hosted page. Ours is `BILLING_RECEIPT_EMAIL=1`: the same moment, from
4truck, with the invoice PDF attached so whoever files it never leaves
their inbox.

One PDF and not two, and that is Stripe's shape rather than a choice:
`invoice.invoice_pdf` is the only document the API hands out. The
second PDF in a Stripe-sent receipt is generated inside Stripe's own
templates; what an API consumer can reach beyond the invoice is
`charge.receipt_url`, an HTML page. So ours attaches the PDF and links
the hosted page.

The order that does not leave a paying customer with nothing:

1. Stripe's on, ours off — go live this way. A receipt is guaranteed.
2. Turn ours on too, for one billing cycle. Read what arrives: the
   words, the attachment, and above all whether it lands in the inbox
   rather than spam (a young sending domain is the real risk here).
3. Only then turn Stripe's off.

Ours never raises: the invoice is recorded before the send, a PDF that
will not download is sent as a link instead, and a mailer that refuses
is logged. A webhook that raised would make Stripe retry the event and
the invoice would be recorded twice.

## 5. Known limits, decided

- A subscription made before per-plan extras Prices stays on the
  env-wide one until the next rollout of its plan moves it (no
  proration; the new amount bills from its next period).
- Feature changes to a plan apply to its accounts at once (~2 minutes
  across workers), not at the period end.
- No tax, no coupons wired (comp accounts are a local overlay).
