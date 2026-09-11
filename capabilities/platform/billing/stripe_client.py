"""Stripe billing provider.

Requires:
  BILLING_PROVIDER=stripe
  STRIPE_SECRET_KEY=sk_live_...
  STRIPE_WEBHOOK_SECRET=whsec_...

Set BILLING_PROVIDER=stub (the default) to disable Stripe entirely.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from capabilities.permissions.plans import account_plan_changed
from capabilities.platform.billing.notifications import (
    notify_checkout_complete as _notify_checkout_complete,
    notify_payment_failed as _notify_payment_failed,
    notify_payment_recovered as _notify_payment_recovered,
)
from infra import observability as _obs

logger = logging.getLogger(__name__)

def _tier_price_id(tier: str) -> str:
    """Resolve tier name → Stripe price id at call time.

    Read lazily (not at import) so tests that monkeypatch ``setenv``
    after the module has loaded see the new value.
    """
    return os.getenv(f"STRIPE_PRICE_{tier.upper()}", "")


def _extra_vehicle_price_id() -> str:
    """The per-extra-vehicle Stripe price id (shared by all tiers)."""
    return os.getenv("STRIPE_PRICE_EXTRA_VEHICLE", "")


def _stripe():
    """Import stripe lazily so the module loads without the package installed."""
    try:
        import stripe as _s
        _s.api_key = os.environ["STRIPE_SECRET_KEY"]
        return _s
    except ImportError:
        raise RuntimeError(
            "stripe package not installed. Run: pip install stripe>=8.0"
        )
    except KeyError:
        raise RuntimeError(
            "STRIPE_SECRET_KEY env var not set. "
            "Set BILLING_PROVIDER=stub to use the stub provider."
        )


class StripeBillingProvider:
    """Stripe-backed billing provider."""

    # ── Summary / info ───────────────────────────────────────────

    async def get_summary(self, account_id: int, db) -> dict:
        return await db.get_billing_summary(account_id, provider="stripe")

    async def get_usage_history(self, account_id: int, db, limit: int = 12) -> list[dict]:
        return await db.get_usage_snapshots(account_id, limit=limit)

    async def record_monthly_snapshot(
        self,
        account_id: int,
        db,
        period_start: str,
        period_end: str,
        vehicle_count: int,
        user_count: int,
        ai_queries: int,
    ) -> int:
        sub = await db.get_or_create_subscription(account_id)
        return await db.record_usage_snapshot(
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
            vehicle_count=vehicle_count,
            user_count=user_count,
            ai_queries=ai_queries,
            base_vehicles=sub["base_vehicles"],
            monthly_base_cents=sub["monthly_base_usd"],
            extra_vehicle_cents=sub["extra_vehicle_cents"],
        )

    # ── Checkout / portal ────────────────────────────────────────

    async def create_checkout_session(
        self,
        account_id: int,
        db,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        stripe = _stripe()
        sub = await db.get_or_create_subscription(account_id)

        # The plan row is the catalog: its Stripe price id first, the env
        # table when the operator has not set one; and a plan the
        # operator hid from the customer's page cannot be bought by name.
        plan = await db.get_plan(tier)
        if plan is None or not plan["public"]:
            raise ValueError(f"Plan '{tier}' is not available.")
        base_price_id = (plan or {}).get("stripe_price_id") or _tier_price_id(tier)
        if not base_price_id:
            raise ValueError(
                f"No Stripe price ID configured for tier '{tier}'. "
                f"Set it on the plan (system console) or the STRIPE_PRICE_{tier.upper()} env var."
            )

        # Get or create Stripe customer
        customer_id = sub.get("provider_customer_id", "")
        if not customer_id:
            customer = stripe.Customer.create(
                email=sub.get("billing_email") or None,
                metadata={"account_id": str(account_id)},
            )
            customer_id = customer["id"]
            await db.update_subscription(
                account_id,
                provider="stripe",
                provider_customer_id=customer_id,
            )

        # Two-line subscription: base tier (fixed quantity 1) + extras
        # (variable quantity, starts at 0).  The extras quantity is
        # nudged later by ``sync_billing_quantity`` once we know the
        # active-vehicle count.  When STRIPE_PRICE_EXTRA_VEHICLE isn't
        # configured we fall back to single-line so older deploys keep
        # working — the dashboard hides the extras footer in that case.
        extras_price_id = _extra_vehicle_price_id()
        line_items: list[dict] = [{"price": base_price_id, "quantity": 1}]
        if extras_price_id:
            line_items.append({"price": extras_price_id, "quantity": 0})

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=line_items,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"account_id": str(account_id), "tier": tier},
        )
        return {"url": session["url"], "session_id": session["id"]}

    async def create_portal_session(
        self,
        account_id: int,
        db,
        return_url: str,
    ) -> dict:
        stripe = _stripe()
        sub = await db.get_or_create_subscription(account_id)
        customer_id = sub.get("provider_customer_id", "")
        if not customer_id:
            raise ValueError(
                "No Stripe customer ID for account — "
                "customer must complete checkout before accessing the portal."
            )
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return {"url": session["url"]}

    # ── Webhook ──────────────────────────────────────────────────

    async def handle_webhook(self, payload: bytes, sig_header: str, db) -> dict:
        stripe = _stripe()
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            # Refuse to process unsigned webhooks in stripe mode.  Letting
            # them through means anyone who can reach /billing/webhook can
            # forge subscription state — flip an account to ``active`` for
            # free or to ``past_due`` to lock real customers out.
            raise RuntimeError(
                "STRIPE_WEBHOOK_SECRET is not set; refusing to process "
                "unsigned Stripe webhooks.  Set the env var or switch "
                "BILLING_PROVIDER to stub."
            )
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except stripe.error.SignatureVerificationError as e:
            logger.warning("Stripe webhook signature verification failed: %s", e)
            _obs.record_billing_webhook("unknown", "invalid_signature")
            raise ValueError("Invalid webhook signature") from e

        event_id = event.get("id", "")
        event_type = event.get("type", "")
        data = event.get("data", {}).get("object", {})

        # Resolve account_id from the event.  Checkout sessions carry
        # ``metadata.account_id`` (we set it when creating the session);
        # invoice / subscription events don't — Stripe generates those
        # objects independently, so we fall back to a lookup by
        # ``customer`` or ``subscription`` id against our subscriptions
        # table.  Returns None when no mapping exists (event for an
        # unknown account, e.g. a stale customer from a deleted account).
        account_id = await self._resolve_account_id(data, db)

        # Idempotency gate — Stripe retries any non-2xx response (and
        # sometimes 2xx on delayed ack), so the same event.id arrives
        # multiple times.  INSERT-OR-IGNORE: first call processes,
        # retries short-circuit with a fast 200 and never re-mutate
        # subscription state.  Events without an id (synthetic / test
        # payloads) skip the gate and run normally.
        if event_id:
            is_new = await db.mark_stripe_event_processed(
                event_id, event_type, account_id
            )
            if not is_new:
                logger.info(
                    "Stripe webhook duplicate ignored: id=%s type=%s",
                    event_id, event_type,
                )
                _obs.record_billing_webhook(event_type, "duplicate")
                return {"handled": True, "event_type": event_type, "duplicate": True}

        if account_id is None:
            logger.warning(
                "Stripe webhook %s could not be matched to an account "
                "(customer=%s subscription=%s); acknowledging without action",
                event_type, data.get("customer", ""), data.get("subscription", ""),
            )
            _obs.record_billing_webhook(event_type, "unmatched")
            return {"handled": False, "event_type": event_type}

        if event_type == "checkout.session.completed":
            tier = (data.get("metadata") or {}).get("tier", "starter")
            sub_id = data.get("subscription", "")
            pricing = await db.pricing_for(tier)
            # Pull the line-item ids so ``sync_billing_quantity`` can
            # PATCH the extras quantity later.  The checkout session
            # payload itself doesn't include them; retrieve the full
            # subscription with items expanded.  If the API call fails
            # (network blip, retry storm) we still update the rest of
            # the record — the item ids backfill on the next
            # ``customer.subscription.updated`` event or sync attempt.
            base_item_id = ""
            extra_item_id = ""
            slots: dict = {}
            if sub_id:
                try:
                    full_sub = stripe.Subscription.retrieve(sub_id, expand=["items"])
                    slots = self._extract_items(full_sub)
                    base_item_id, extra_item_id = slots["base"]["id"], slots["extra"]["id"]
                except Exception:
                    logger.exception(
                        "Stripe Subscription.retrieve(%s) failed during checkout completion; "
                        "item ids and Stripe's prices will backfill on the next subscription.updated",
                        sub_id,
                    )
            updates = {
                "tier": tier,
                "status": "active",
                "provider_subscription_id": sub_id,
                # prices as Stripe will invoice them (the plan row when the
                # retrieve failed or the price is not a plain monthly USD one)
                **self._priced_updates(tier, pricing, slots),
            }
            if base_item_id:
                updates["provider_base_item_id"] = base_item_id
                updates["provider_base_price_id"] = (slots.get("base") or {}).get("price_id") or ""
            if extra_item_id:
                updates["provider_extra_item_id"] = extra_item_id
            await db.update_subscription(account_id, **updates)
            await db.update_account_tier(account_id, tier)
            account_plan_changed(account_id)
            logger.info("Checkout complete: account=%s tier=%s", account_id, tier)
            await self._safe_notify(
                "checkout", _notify_checkout_complete, account_id, tier,
            )

        elif event_type == "customer.subscription.updated":
            status = data.get("status", "active")
            period_start = data.get("current_period_start")
            period_end   = data.get("current_period_end")
            updates = {
                "status": status,
                "current_period_start": str(period_start) if period_start else None,
                "current_period_end":   str(period_end)   if period_end   else None,
            }
            # Maintain the past_due_since invariant — set when entering,
            # clear when leaving — so the enforcement grace check has a
            # reliable timestamp to compare against.
            row = await db.get_subscription(account_id)
            updates.update(self._past_due_since_fields(status, row))
            # Backfill, never refresh: a row whose extras item id is still
            # blank (the checkout-time retrieve failed) takes the item ids
            # and Stripe's prices from this event's own Subscription
            # object, which carries items.data[].price without a retrieve.
            # A row that already has them is left alone here.
            if row and not (row.get("provider_extra_item_id") or ""):
                slots = self._extract_items(data)
                if slots["base"]["id"] or slots["extra"]["id"]:
                    tier = row.get("tier") or data.get("metadata", {}).get("tier") or "free"
                    pricing = await db.pricing_for(tier)
                    updates.update(self._priced_updates(tier, pricing, slots))
                    if slots["base"]["id"]:
                        updates["provider_base_item_id"] = slots["base"]["id"]
                        updates["provider_base_price_id"] = slots["base"].get("price_id") or ""
                    if slots["extra"]["id"]:
                        updates["provider_extra_item_id"] = slots["extra"]["id"]
                    logger.info("Stripe item ids backfilled from subscription.updated: account=%s", account_id)
            await db.update_subscription(account_id, **updates)

        elif event_type == "customer.subscription.deleted":
            await db.update_subscription(
                account_id, status="canceled", past_due_since=None
            )
            await db.update_account_tier(account_id, "free")
            account_plan_changed(account_id)
            logger.info("Subscription canceled: account=%s", account_id)

        elif event_type == "invoice.payment_succeeded":
            await db.record_invoice(account_id, **self._invoice_fields(data))
            # Lift past_due once a payment lands.  We don't touch other
            # statuses (active / trialing / canceled) — a paid invoice
            # against a canceled subscription is rare but real (final
            # proration); flipping back to active would be wrong.
            sub = await db.get_subscription(account_id)
            if sub and sub.get("status") == "past_due":
                await db.update_subscription(
                    account_id, status="active", past_due_since=None
                )
                logger.info("Payment recovered, account=%s back to active", account_id)
                await self._safe_notify(
                    "payment_recovered", _notify_payment_recovered,
                    account_id, int(data.get("amount_paid", 0) or 0),
                )

        elif event_type == "invoice.payment_failed":
            await db.record_invoice(account_id, **self._invoice_fields(data))
            sub = await db.get_subscription(account_id)
            updates = {"status": "past_due"}
            updates.update(self._past_due_since_fields("past_due", sub))
            await db.update_subscription(account_id, **updates)
            await self._safe_notify(
                "payment_failed", _notify_payment_failed,
                account_id,
                int(data.get("amount_due", 0) or 0),
                data.get("hosted_invoice_url", "") or "",
            )

        _obs.record_billing_webhook(event_type, "processed")
        return {"handled": True, "event_type": event_type}

    @staticmethod
    async def _safe_notify(label: str, fn, *args) -> None:
        """Fire a notification helper without letting it poison the webhook.

        Webhook handlers must always return 2xx promptly or Stripe will
        retry the whole event — including the DB writes we already
        committed.  Catching here keeps the notification channel
        best-effort: the DB is the source of truth and notifications
        are a courtesy on top.
        """
        try:
            await fn(*args)
        except Exception:
            logger.exception("billing notification %s raised; ignoring", label)

    @staticmethod
    def _extract_items(stripe_sub: Any) -> dict:
        """Match subscription items to the base / extras slots, with the
        PRICE behind each — id, unit amount, interval, currency — as
        Stripe carries it on an expanded item.

        The extras item is the one whose price id matches
        STRIPE_PRICE_EXTRA_VEHICLE; the first other item is the base.
        A slot we cannot match is ``{"id": ""}`` — callers treat a blank
        id as "not configured yet" and skip the corresponding sync.
        """
        slots: dict = {"base": {"id": ""}, "extra": {"id": ""}}
        items = (stripe_sub.get("items") or {}).get("data") if isinstance(stripe_sub, dict) else None
        if items is None and hasattr(stripe_sub, "items"):
            try:
                items = stripe_sub["items"]["data"]
            except (KeyError, TypeError):
                items = []
        extras_price = _extra_vehicle_price_id()
        for item in (items or []):
            price = item.get("price") or {}
            if not isinstance(price, dict):
                price = {}
            rec = price.get("recurring") or {}
            slot = {
                "id": item.get("id", ""),
                "price_id": price.get("id", ""),
                "unit_amount": price.get("unit_amount"),
                "interval": (rec.get("interval") if isinstance(rec, dict) else None),
                "interval_count": (rec.get("interval_count") if isinstance(rec, dict) else None),
                "currency": price.get("currency"),
            }
            if extras_price and slot["price_id"] == extras_price:
                slots["extra"] = slot
            elif not slots["base"]["id"]:
                # First non-extras item is the base.  Robust to either
                # ordering Stripe returns the items in, and ignores
                # any future add-on items we might attach without a
                # schema column for.
                slots["base"] = slot
        return slots

    @staticmethod
    def _extract_item_ids(stripe_sub: Any) -> tuple[str, str]:
        """``(base_item_id, extras_item_id)`` — see ``_extract_items``."""
        slots = StripeBillingProvider._extract_items(stripe_sub)
        return slots["base"]["id"], slots["extra"]["id"]

    @staticmethod
    def _stripe_monthly_usd(slot: dict) -> int | None:
        """The amount Stripe will invoice for this slot, when it is the
        shape our columns mean — a whole number of cents, monthly, USD.
        Anything else (a yearly price, a tiered or decimal amount, a
        missing price) is ``None`` and the plan row answers instead;
        boring wins over clever."""
        amt = slot.get("unit_amount")
        if not isinstance(amt, int) or isinstance(amt, bool) or amt < 0:
            return None
        if slot.get("interval") != "month" or (slot.get("interval_count") or 1) != 1:
            return None
        if str(slot.get("currency") or "").lower() != "usd":
            return None
        return amt

    @staticmethod
    def _priced_updates(tier: str, pricing: dict, slots: dict) -> dict:
        """What a subscription write records for its prices: Stripe's
        amount for each slot when it is the shape we mean (that is what
        the customer is invoiced), else the plan row's; the trucks
        included are always the row's — Stripe has no such concept.
        A difference between the two is logged loudly: it is the plan
        row lying, or the Stripe price swapped under it."""
        out = {"base_vehicles": pricing["base_vehicles"]}
        for slot_name, column, row_key in (
            ("base", "monthly_base_usd", "monthly_base_cents"),
            ("extra", "extra_vehicle_cents", "extra_vehicle_cents"),
        ):
            stripe_amt = StripeBillingProvider._stripe_monthly_usd(slots.get(slot_name) or {})
            row_amt = int(pricing.get(row_key) or 0)
            if stripe_amt is None:
                out[column] = row_amt
            else:
                out[column] = stripe_amt
                if stripe_amt != row_amt:
                    logger.warning(
                        "plan price drift tier=%s slot=%s row=%s stripe=%s — the plan row "
                        "(system console) does not match the Stripe price; Stripe's amount is recorded",
                        tier, slot_name, row_amt, stripe_amt,
                    )
        return out

    # ── the plan's price, and its rollout ─────────────────────────

    async def create_plan_price(self, *, tier: str, label: str, cents: int, before: dict) -> dict:
        from capabilities.platform.billing import rollout as _rollout
        return _rollout.ensure_plan_price(_stripe(), tier=tier, label=label, cents=cents, before=before)

    def archive_plan_price(self, price_id: str) -> bool:
        from capabilities.platform.billing import rollout as _rollout
        return _rollout.archive_price(_stripe(), price_id)

    async def rollout_preview(self, db, tier: str) -> dict:
        from capabilities.platform.billing import rollout as _rollout
        return await _rollout.preview(db, tier)

    async def rollout_execute(self, db, tier: str, *, actor: str) -> dict:
        from capabilities.platform.billing import rollout as _rollout
        return await _rollout.execute(_stripe(), db, tier, actor=actor)

    async def update_billing_email(self, account_id: int, db, email: str) -> dict:
        """Persist the email locally and push it to Stripe's Customer record.

        Stripe sends invoice receipts and dunning emails to the address
        on the Customer object, so getting this wrong silently breaks
        payment-recovery flows.  We always write the local row first;
        if the account already has a Stripe customer, we PATCH there
        too.  An accountant who hasn't completed checkout yet gets the
        local write only — their Stripe customer (created at checkout)
        will pick up the saved email on first creation.
        """
        await db.get_or_create_subscription(account_id)
        await db.update_subscription(account_id, billing_email=email)
        sub = await db.get_subscription(account_id)
        customer_id = (sub or {}).get("provider_customer_id", "")
        if not customer_id:
            return {
                "account_id": account_id, "email": email,
                "synced_to_provider": False, "reason": "no_stripe_customer",
            }
        try:
            stripe = _stripe()
            stripe.Customer.modify(customer_id, email=email)
            return {
                "account_id": account_id, "email": email,
                "synced_to_provider": True,
            }
        except Exception:
            logger.exception(
                "update_billing_email: Stripe Customer.modify failed acct=%s",
                account_id,
            )
            return {
                "account_id": account_id, "email": email,
                "synced_to_provider": False, "reason": "stripe_error",
            }

    async def sync_billing_quantity(self, account_id: int, db) -> dict:
        """Reconcile Stripe's extras-line quantity with our active count.

        Called after every Samsara ingest so an account that scales up
        (or parks trucks) sees the change on the next Stripe invoice.
        The flow:

          1. ``BillingMixin.compute_billing`` gives us the target qty.
          2. We compare to the current Stripe subscription_item qty.
          3. Only PATCH if it changed — most calls are no-ops.
          4. Skip silently for accounts that aren't on Stripe yet
             (stub provider, comped without checkout, missing item id).

        Returns a small status dict for logs / metrics: ``{skipped: str,
        before, after, account_id}`` so the caller can wire it into a
        Prometheus counter without crashing if Stripe is unreachable.
        """
        sub = await db.get_subscription(account_id)
        if not sub:
            _obs.record_sync_billing_quantity("no_subscription")
            return {"skipped": "no_subscription", "account_id": account_id}
        if (sub.get("provider") or "") != "stripe":
            _obs.record_sync_billing_quantity("not_stripe")
            return {"skipped": "not_stripe", "account_id": account_id}
        extra_item_id = sub.get("provider_extra_item_id") or ""
        if not extra_item_id:
            _obs.record_sync_billing_quantity("no_extras_item")
            return {"skipped": "no_extras_item", "account_id": account_id}
        billing = await db.compute_billing(account_id)
        target_qty = billing["extras"]
        stripe = _stripe()
        try:
            current_item = stripe.SubscriptionItem.retrieve(extra_item_id)
            current_qty = int(current_item.get("quantity", 0) or 0)
            if current_qty == target_qty:
                _obs.record_sync_billing_quantity("noop")
                return {
                    "skipped": "noop",
                    "before": current_qty, "after": target_qty,
                    "account_id": account_id,
                }
            stripe.SubscriptionItem.modify(
                extra_item_id,
                quantity=target_qty,
                # Stripe pro-rates the difference by default; we keep
                # the default so a mid-cycle scale-up is fair to the
                # customer (only charged for the days they actually
                # had the trucks active).
                proration_behavior="create_prorations",
            )
            _obs.record_sync_billing_quantity("patched")
            logger.info(
                "sync_billing_quantity: account=%s extras %s → %s",
                account_id, current_qty, target_qty,
            )
            return {
                "skipped": None,
                "before": current_qty, "after": target_qty,
                "account_id": account_id,
            }
        except Exception:
            _obs.record_sync_billing_quantity("stripe_error")
            logger.exception(
                "sync_billing_quantity: Stripe PATCH failed for account=%s "
                "(item=%s target_qty=%s)",
                account_id, extra_item_id, target_qty,
            )
            return {"skipped": "stripe_error", "account_id": account_id}

    @staticmethod
    def _past_due_since_fields(new_status: str, current: dict | None) -> dict:
        """Compute ``past_due_since`` updates for a status transition.

        Sets it on first entry to past_due (preserving an existing
        timestamp if we're already past_due — multiple retry failures
        shouldn't reset the grace clock).  Clears it when moving back
        to anything else.  Returns an empty dict if no change is needed,
        so callers can ``**`` it into update_subscription without
        accidentally overwriting an already-correct value.
        """
        cur_since = (current or {}).get("past_due_since")
        if new_status == "past_due":
            if cur_since:
                return {}  # preserve original
            from datetime import datetime, timezone
            return {"past_due_since": datetime.now(timezone.utc).isoformat()}
        # Any other status — clear the marker (but only if currently set)
        if cur_since:
            return {"past_due_since": None}
        return {}

    # ── Webhook helpers ──────────────────────────────────────────

    @staticmethod
    async def _resolve_account_id(data: dict, db) -> int | None:
        """Find our account_id for a Stripe event payload.

        Order: explicit ``metadata.account_id`` (checkout sessions) →
        lookup by ``customer`` id → lookup by ``subscription`` id.  The
        customer/subscription path matters for invoice events, which
        Stripe generates without our metadata.
        """
        meta = (data.get("metadata") or {})
        if (raw := meta.get("account_id")):
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        if (cust := data.get("customer", "")):
            if (acct := await db.find_account_by_stripe_customer(cust)):
                return acct
        if (sub := data.get("subscription", "")):
            if (acct := await db.find_account_by_stripe_subscription(sub)):
                return acct
        return None

    @staticmethod
    def _invoice_fields(invoice: dict) -> dict:
        """Project a Stripe Invoice payload onto our ``billing_invoices`` schema."""
        # Stripe uses unix-epoch seconds for invoice timestamps; we
        # store ISO-8601 UTC for grep-friendliness in the dashboard.
        def _to_iso(val) -> str | None:
            if not val:
                return None
            try:
                return datetime.fromtimestamp(int(val), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                return None

        # ``period_start`` / ``period_end`` live on the invoice line items
        # for subscription invoices.  Fall back to the invoice-level
        # ``period_*`` for one-off invoices (older Stripe API versions).
        lines = (invoice.get("lines") or {}).get("data") or []
        first_line = lines[0] if lines else {}
        line_period = first_line.get("period") or {}
        paid_at = (invoice.get("status_transitions") or {}).get("paid_at")
        return {
            "provider_invoice_id":      invoice.get("id", ""),
            "provider_subscription_id": invoice.get("subscription", "") or "",
            "provider_customer_id":     invoice.get("customer", "") or "",
            "amount_due_cents":         int(invoice.get("amount_due", 0) or 0),
            "amount_paid_cents":        int(invoice.get("amount_paid", 0) or 0),
            "currency":                 (invoice.get("currency") or "usd").lower(),
            "status":                   invoice.get("status", "") or "",
            "period_start": _to_iso(line_period.get("start") or invoice.get("period_start")),
            "period_end":   _to_iso(line_period.get("end")   or invoice.get("period_end")),
            "hosted_invoice_url":       invoice.get("hosted_invoice_url", "") or "",
            "invoice_pdf_url":          invoice.get("invoice_pdf", "") or "",
            "paid_at":                  _to_iso(paid_at),
        }
