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

#: Where a trigger may be sent.  ``in_app`` is absent on purpose: the bell
#: record is not a channel a person turns off, because a trigger that
#: fired and left no trace is indistinguishable from one that never fired.
#: These are the EXTRA places it can reach you.
TRIGGER_CHANNELS = ("telegram_dm", "email", "web_push")
#: The bell always gets it, so it is prepended at delivery, never stored.
ALWAYS = "in_app"
#: What a new trigger reaches when nobody said otherwise.  Push is absent
#: because it needs a subscribed browser: a default that silently depends
#: on setup someone has not done reads as "I ticked it and nothing came".
DEFAULT_CHANNELS = ("telegram_dm", "email")
#: The stored form of that default — one definition, so the column
#: default, the dataclass default and the create route cannot drift.
DEFAULT_CHANNELS_CSV = ",".join(DEFAULT_CHANNELS)


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
    #: csv of TRIGGER_CHANNELS — the extra places beyond the bell.
    channels: str = DEFAULT_CHANNELS_CSV
    #: csv of ``vehicles.id`` — which vehicles this trigger watches.
    #: '' means EVERY vehicle in the owner's scope, which is what a
    #: trigger meant before targeting existed and still means.
    vehicles: str = ""

    @property
    def target_ids(self) -> list[int]:
        """The registry ids this trigger watches, or [] for "all mine".

        Non-numeric junk is dropped rather than raising: this list is
        data a client sent, and one bad entry must narrow the selection,
        never take the sweep down for the whole account.

        De-duplicated on READ as well as on write.  ``clean_vehicle_ids``
        already dedupes what it stores, but a row predating it — or one
        edited by hand — would otherwise report "3 vehicles" for two, and
        the count is a number a person checks their work against.
        """
        out: list[int] = []
        for part in (self.vehicles or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value not in out:
                out.append(value)
        return out

    @property
    def targets_all(self) -> bool:
        """True when this trigger watches everything the owner can see."""
        return not self.target_ids

    @property
    def chosen_channels(self) -> list[str]:
        """The stored csv as a list.  SPLIT, never substring-matched: a
        future key that is a substring of another ("push" beside
        "web_push") would otherwise report itself as chosen by a row that
        never picked it."""
        picked = {c.strip() for c in (self.channels or "").split(",")}
        return [c for c in TRIGGER_CHANNELS if c in picked]

    @property
    def delivery_channels(self) -> list[str]:
        """The channel list ``notify_user`` is given: the bell first,
        always, then whatever this trigger asked for."""
        return [ALWAYS, *self.chosen_channels]

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
        return f"{m.label} {m.direction} {num_text(self.threshold)}{m.unit}"

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
            # ``is None``, never ``or``: an empty string is the legal
            # BELL-ONLY choice, and coercing it to the default would keep
            # DMing someone who explicitly unticked every channel.
            channels=(str(row["channels"]) if row.get("channels") is not None
                      else DEFAULT_CHANNELS_CSV),
            vehicles=str(row.get("vehicles") or ""),
        )


def clean_channels(raw) -> str:
    """A caller's channel list → the csv this row stores.

    Unknown keys are dropped rather than refused: a client sending a
    channel this build does not have should lose that one choice, not the
    whole save.  An empty result is legal and means bell-only — the one
    delivery nobody can switch off.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    seen = [str(c).strip() for c in (raw or [])]
    return ",".join(c for c in TRIGGER_CHANNELS if c in seen)


def clean_vehicle_ids(raw) -> str:
    """A caller's vehicle selection → the csv this row stores.

    Ints only, de-duplicated, order preserved.  Anything that is not a
    positive integer is DROPPED rather than refused, for the same reason
    ``clean_channels`` drops an unknown channel: a client sending one bad
    id should lose that one vehicle, not the whole save.  An empty result
    is legal and is the "all my vehicles" default.

    This does NOT check that the ids exist or that the caller may see
    them — that is the router's job, because it needs the database and
    the caller's scope, and neither belongs in a shape helper.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    seen: list[int] = []
    for item in (raw or []):
        try:
            value = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in seen:
            seen.append(value)
    return ",".join(str(v) for v in seen)


def num_text(value: float) -> str:
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
