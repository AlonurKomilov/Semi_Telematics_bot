"""Driver Pay computation engine — pure-ish: pulls scorecards + safety events,
applies bonus rules, returns RunItem objects.  Not yet persisted.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from capabilities.scorecards.service import evaluate_subjects
from features.loads import service as loads_service
from infra.services import get_tenant_db

from .models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    BonusRule,
    BreakdownEntry,
    RunItem,
)

log = logging.getLogger(__name__)


def _row_to_rule(row: dict) -> BonusRule:
    return BonusRule(
        id=int(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        amount_cents=int(row["amount_cents"]),
        period_days=int(row["period_days"] or 30),
        score_min=(float(row["score_min"]) if row.get("score_min") is not None else None),
        event_type=row.get("event_type") or None,
        max_count=(int(row["max_count"]) if row.get("max_count") is not None else None),
        active=bool(row.get("active", 1)),
    )


PAY_MODEL_PERCENTAGE = "percentage"
PAY_MODEL_PER_MILE = "per_mile"
VALID_PAY_MODELS = ("", PAY_MODEL_PERCENTAGE, PAY_MODEL_PER_MILE)


def _load_pay_cents(l: dict, settings: Optional[dict]) -> int:
    """One delivered load's driver pay, in cents.

    A stored figure wins (pinned real pay, or the Datatruck tariff
    estimate the projector wrote); otherwise the driver's pay model
    fills in: percentage-of-rate (28 and 0.28 both accepted) or
    per-mile × total miles.  No model, no figure → 0 (the operator sees
    a zero-earnings load on the statement rather than invented money).
    """
    if l.get("driver_pay") is not None:
        return int(round(float(l["driver_pay"]) * 100))
    if not settings:
        return 0
    model = str(settings.get("pay_model") or "").strip().lower()
    try:
        rate = float(settings.get("pay_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        return 0
    if model == PAY_MODEL_PERCENTAGE:
        frac = rate / 100.0 if rate > 1 else rate
        basis = float(l.get("total_rate") or 0)
        if 0 < frac < 1 and basis > 0:
            return int(round(basis * frac * 100))
        return 0
    if model == PAY_MODEL_PER_MILE:
        miles = float(l.get("total_miles") or 0)
        return int(round(miles * rate * 100)) if miles > 0 else 0
    return 0


def _period_days(period_start: date, period_end: date) -> int:
    days = (period_end - period_start).days + 1
    return max(days, 1)


def evaluate_score_rule(
    rule: BonusRule, score: Optional[float],
) -> Optional[BreakdownEntry]:
    """Return a BreakdownEntry if the driver qualifies, else None."""
    if rule.kind != KIND_SCORE_THRESHOLD or rule.score_min is None:
        return None
    if score is None:
        return None
    if score >= rule.score_min:
        return BreakdownEntry(
            rule_id=rule.id,
            name=rule.name,
            kind=rule.kind,
            amount_cents=rule.amount_cents,
            detail=f"score {score:.0f} ≥ {rule.score_min:.0f}",
        )
    return None


def evaluate_incident_rule(
    rule: BonusRule, incident_count: int,
) -> Optional[BreakdownEntry]:
    if rule.kind != KIND_INCIDENT_COUNT or rule.max_count is None:
        return None
    if incident_count <= rule.max_count:
        et = rule.event_type or "any"
        return BreakdownEntry(
            rule_id=rule.id,
            name=rule.name,
            kind=rule.kind,
            amount_cents=rule.amount_cents,
            detail=f"{et} incidents={incident_count} ≤ {rule.max_count}",
        )
    return None


async def compute_run(
    account_id: int,
    period_start: date,
    period_end: date,
) -> list[RunItem]:
    """Compute driver pay for one period.

    Pulls active bonus_rules + driver_pay_settings + scorecards over
    the period window; for incident_count rules, queries warehouse
    safety events filtered by event_type.  Returns one RunItem per
    opted-in driver with at least base pay or one qualifying bonus.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []

    days = _period_days(period_start, period_end)

    # Active bonus rules + per-driver settings
    rule_rows = await tenant.list_bonus_rules(account_id, active_only=True)
    rules = [_row_to_rule(r) for r in rule_rows]

    settings_rows = await tenant.list_driver_pay_settings(account_id)
    settings_by_driver: dict[str, dict] = {
        str(r["driver_id"]): r for r in settings_rows
    }

    # ── Load earnings + extra pay items (the settlements core) ──
    # Read through the loads service (service-contract rule; limit=None
    # is the aggregation contract).  Loads key people by users.id — the
    # bridge into driver-pay's samsara-keyed world is
    # users.samsara_driver_id; drivers without one (Datatruck-only /
    # manual) get a "user:{id}" key so their statement still exists.
    start_iso, end_iso = period_start.isoformat(), period_end.isoformat()
    period_loads = await loads_service.get_loads(
        account_id, since=start_iso, until=end_iso, limit=None,
    )
    # Settlement line items (on-load + off-load), tagged by bucket:
    # driver_pay = ADDITIONS, deduction = DEDUCTIONS — one unified
    # mechanism, itemized so the statement lists each line.
    settlement_items = await loads_service.get_settlement_items(
        account_id, since=start_iso, until=end_iso,
    )
    users = await tenant.list_account_users(account_id)
    uid_to_sid = {
        u.id: str(getattr(u, "samsara_driver_id", "") or "").strip()
        for u in users
    }
    uname_by_uid = {u.id: (u.display_name or f"user {u.id}") for u in users}

    def _key_for_uid(uid: int) -> str:
        return uid_to_sid.get(uid) or f"user:{uid}"

    comp_by_key: dict[str, dict] = {}

    def _comp(uid: int) -> dict:
        return comp_by_key.setdefault(_key_for_uid(uid), {
            "uid": uid, "earnings": 0, "extras": 0, "loads": 0,
            "deductions": 0, "zero_pay_loads": 0,
            "load_lines": [], "addition_lines": [], "deduction_lines": [],
        })

    for l in period_loads:
        if l.get("status") != "delivered":
            continue
        uid = l.get("driver_user_id")
        if not uid:
            continue
        c = _comp(int(uid))
        pay_cents = _load_pay_cents(
            l, settings_by_driver.get(uid_to_sid.get(int(uid), "")),
        )
        c["earnings"] += pay_cents
        c["loads"] += 1
        # A delivered load that resolves to $0 (no stored pay, no pay
        # model) silently underpays — count it so the statement can warn.
        if pay_cents == 0:
            c["zero_pay_loads"] += 1
        origin = str(l.get("pickup_location") or "")
        dest = str(l.get("delivery_location") or "")
        c["load_lines"].append({
            "date": l.get("delivery_date") or l.get("pickup_date") or "",
            "load_number": l.get("load_number") or "",
            "route": f"{origin} → {dest}".strip(" →"),
            "rate_cents": int(round(float(l.get("total_rate") or 0) * 100)),
            "pay_cents": pay_cents,
        })
    for it in settlement_items:
        uid = it.get("driver_user_id")
        if not uid:
            continue
        c = _comp(int(uid))
        amt = int(round(float(it.get("amount") or 0) * 100))
        line = {
            "date": it.get("item_date") or "",
            "kind": it.get("kind") or "other",
            "amount_cents": amt,
            "notes": it.get("notes") or "",
            "load_number": it.get("load_number") or "",
        }
        if it.get("bucket") == "deduction":
            c["deductions"] += amt
            c["deduction_lines"].append(line)
        else:
            c["extras"] += amt
            c["addition_lines"].append(line)

    # Composite scorecards over the period
    cards = await evaluate_subjects(
        account_id, subject="driver", days=days, write_evidence=False,
    )
    cards_by_driver: dict[str, dict] = {}
    for c in cards:
        did = str(c.get("driver_id") or "").strip()
        if did:
            cards_by_driver[did] = c

    # Pre-pull incident counts per (driver, event_type) for incident rules
    incident_event_types = {
        r.event_type for r in rules
        if r.kind == KIND_INCIDENT_COUNT and r.event_type
    }
    has_any_kind = any(
        r.kind == KIND_INCIDENT_COUNT and not r.event_type for r in rules
    )

    # Drivers we will iterate: union of opted-in pay settings + drivers
    # with cards + drivers with load earnings / pay items in the period.
    driver_ids = set(settings_by_driver.keys()) | set(cards_by_driver.keys()) \
        | set(comp_by_key.keys())

    # Cache: counts_by_driver[driver_id][event_type or "*"] = int
    counts_by_driver: dict[str, dict[str, int]] = {}

    if (incident_event_types or has_any_kind) and driver_ids:
        # Bulk-fetch counts for typed event rules (one grouped query
        # replaces D × E queries — same pattern as coaching/engine.py).
        if incident_event_types:
            typed_counts = await tenant.get_safety_event_counts_grouped(
                account_id,
                days=days,
                event_types=[et for et in incident_event_types if et],
                driver_ids=list(driver_ids),
            )
            for (did, et), cnt in typed_counts.items():
                counts_by_driver.setdefault(did, {})[et or ""] = cnt
        # "Any-kind" rules need the per-driver total across all event
        # types — one ungrouped query collapses D queries to 1.
        if has_any_kind:
            any_counts = await tenant.get_safety_event_counts_grouped(
                account_id,
                days=days,
                event_types=None,
                driver_ids=list(driver_ids),
            )
            totals: dict[str, int] = {}
            for (did, _et), cnt in any_counts.items():
                totals[did] = totals.get(did, 0) + cnt
            for did, total in totals.items():
                counts_by_driver.setdefault(did, {})["*"] = total

    items: list[RunItem] = []
    for did in sorted(driver_ids):
        settings = settings_by_driver.get(did) or {}
        opt_in = bool(settings.get("opt_in", 1))
        if not opt_in:
            continue
        base_pay = int(settings.get("base_pay_cents", 0) or 0)
        card = cards_by_driver.get(did)
        score = float(card["score"]) if card and card.get("score") is not None else None
        comp = comp_by_key.get(did) or {}
        driver_name = (
            (card.get("driver_name") if card else None)
            or (uname_by_uid.get(comp.get("uid")) if comp else None)
            or settings.get("driver_id")
            or did
        )

        breakdown: list[BreakdownEntry] = []
        for rule in rules:
            entry: Optional[BreakdownEntry] = None
            if rule.kind == KIND_SCORE_THRESHOLD:
                entry = evaluate_score_rule(rule, score)
            elif rule.kind == KIND_INCIDENT_COUNT:
                key = rule.event_type or "*"
                count = (counts_by_driver.get(did) or {}).get(key, 0)
                entry = evaluate_incident_rule(rule, count)
            if entry is not None:
                breakdown.append(entry)

        bonus_total = sum(b.amount_cents for b in breakdown)

        load_earnings = int(comp.get("earnings") or 0)
        extras = int(comp.get("extras") or 0)
        loads_count = int(comp.get("loads") or 0)
        if load_earnings or loads_count:
            breakdown.append(BreakdownEntry(
                name="Load earnings", kind="load_earnings",
                amount_cents=load_earnings,
                detail=f"{loads_count} delivered load"
                       f"{'' if loads_count == 1 else 's'}",
            ))
        if extras:
            breakdown.append(BreakdownEntry(
                name="Extra pay items", kind="extra_items",
                amount_cents=extras,
                detail="layover / TONU / detention / bonus",
            ))
        total = base_pay + bonus_total + load_earnings + extras
        deductions = int(comp.get("deductions") or 0)
        net = total - deductions

        # Skip drivers with nothing at all to keep runs clean.
        if total <= 0 and deductions <= 0 and not breakdown:
            continue

        items.append(RunItem(
            driver_id=did,
            driver_user_id=comp.get("uid"),
            driver_name=str(driver_name),
            zero_pay_loads=int(comp.get("zero_pay_loads") or 0),
            base_pay_cents=base_pay,
            bonus_total_cents=bonus_total,
            total_cents=total,
            breakdown=breakdown,
            load_earnings_cents=load_earnings,
            extras_cents=extras,
            loads_count=loads_count,
            load_lines=list(comp.get("load_lines") or []),
            addition_lines=list(comp.get("addition_lines") or []),
            deductions_cents=deductions,
            net_cents=net,
            deduction_lines=list(comp.get("deduction_lines") or []),
        ))

    return items


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
