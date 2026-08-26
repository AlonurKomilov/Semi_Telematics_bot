"""What an alert trigger IS — the row, and what may be said about it.

An ``AlertTrigger`` is one person's sentence: *"tell me when DEF drops
below 10% on my trucks."*  It carries no schedule and no comparator: the
metric owns its direction and its check cadence (``catalog.py``), so the
only thing this row holds that the catalog does not is WHO wants it, WHICH
metric, and AT WHAT NUMBER.

Two columns exist before they are used, deliberately.

``scope``
    ``personal`` today, and the API refuses ``account``.  Personal
    triggers deliver by DM only and never write an ``alert_history``
    row, so they cannot add to a board that already carries thousands of
    unacknowledged alerts.

    Account scope was mapped rather than guessed: every built-in checker
    was read to find what it actually fires on, and the answer per metric
    now lives in the catalog.  Fuel, DEF and coolant are REFUSED — a
    built-in check already alerts the whole account on each, so a second
    watcher would be two alerts for one event.  Battery voltage and oil
    pressure are free: nothing in the tree produces either, and both have
    labels and escalation titles for alerts the product has never sent.

    Those two are still gated, for a reason that is about the BOARD and
    not about them: every fire threads a timestamp into its dedup key, so
    repeats never collapse and 85% of rows are never acknowledged. New
    writers wait for that.

``origin``
    ``user`` for something a person wrote, ``seeded`` for a default the
    platform put there.  Nothing seeds today: two live sources of truth
    for fuel would double-fire.  The column exists so the later
    absorption — a built-in checker's env threshold becoming an ordinary
    account trigger — is a data migration and not a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from capabilities.alerting.triggers.catalog import (
    Metric, account_scope_error, get_metric, settable_error,
)

#: One person's worth of watching.  Not a technical limit — a cap that
#: keeps a runaway UI (or a script) from turning one account's sweep into
#: hundreds of evaluations.
MAX_TRIGGERS_PER_USER = 20

SCOPES = ("personal", "account")
ORIGINS = ("user", "seeded")


@dataclass(frozen=True)
class AlertTrigger:
    id: int
    account_id: int
    owner_user_id: int
    metric: str
    threshold: float
    scope: str = "personal"
    origin: str = "user"
    enabled: bool = True
    severity: str = "warning"

    @property
    def spec(self) -> Metric | None:
        """The catalog entry this trigger names, or None if the metric was
        retired from the catalog while rows still referenced it."""
        return get_metric(self.metric)

    def describe(self) -> str:
        """The sentence a human reads — "DEF level below 10%"."""
        m = self.spec
        if m is None:
            return f"{self.metric} {self.threshold}"
        return f"{m.label} {m.direction} {_num(self.threshold)}{m.unit}"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AlertTrigger":
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            owner_user_id=int(row["owner_user_id"]),
            metric=str(row["metric"]),
            threshold=float(row["threshold"]),
            scope=str(row.get("scope") or "personal"),
            origin=str(row.get("origin") or "user"),
            enabled=bool(row.get("enabled", True)),
            severity=str(row.get("severity") or "warning"),
        )


def _num(value: float) -> str:
    """12.0 → "12", 12.5 → "12.5" — a threshold reads as the number the
    person typed, not as a float."""
    return str(int(value)) if float(value).is_integer() else str(value)


def validate(metric_key: str, threshold: Any, scope: str = "personal") -> str:
    """'' when this would be a usable trigger, else why it would not.

    Refusals are the catalog's, not this module's: an unknown metric is
    refused because the catalog is a whitelist, and an out-of-range
    threshold is refused with the reason the metric itself declares.
    """
    metric = get_metric(metric_key)
    if metric is None:
        return f"{metric_key!r} is not a metric that can be watched"
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return "The threshold has to be a number"
    if scope not in SCOPES:
        return f"{scope!r} is not a scope"
    if scope == "account":
        # Two questions, and they refuse for different reasons.  First:
        # does something ALREADY alert the whole account on this metric?
        # Where one does, a second watcher is two alerts for one event —
        # the catalog carries that verdict per metric, mapped from what
        # each built-in checker actually fires on.
        blocked = account_scope_error(metric)
        if blocked:
            return blocked
        # Second: even where the metric is free, account triggers write
        # to the shared Alerts board, and the board's consolidation is
        # broken — every fire threads a timestamp into its dedup key, so
        # 12,528 of 12,595 rows sit at one occurrence and 85% are never
        # acknowledged.  Adding writers to that is adding noise.  The
        # metric-level verdicts above are settled and recorded; this
        # gate lifts when the board can collapse repeats again.
        return ("Account-wide triggers are not switched on yet — they post "
                "to the shared Alerts board, and that board needs its "
                "repeat-collapsing fixed first")
    return settable_error(metric, value)
