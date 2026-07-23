"""Notification category registry — the source-agnostic topic catalog.

A **category** is what a notification is ABOUT, namespaced by its source:
``alert.faults``, ``system.billing_renewed``, ``team.invite_accepted``.
Each event source registers its own categories here (idiomatic registry,
like channels/tools/artifacts), so the notification core never hardcodes
"an alert type" — it just routes categories.

This module imports only stdlib — never an event source — so it stays on
the notification side of the layer boundary.  Sources register FROM their
own side (alerting imports notifications, never the reverse):

    register_category(NotificationCategory(
        key="alert.faults", label="Engine faults",
        kind=BROADCAST, audience=lambda role: role_can_receive_alert(role, "faults"),
    ))

Two `kind`s, because the recipient model differs:
  • BROADCAST — fan out to everyone whose ROLE is eligible (``audience``)
    and who opted in.  Alerts.
  • TARGETED  — one specific user (the actor / affected person), opt-OUT
    (delivered unless muted).  Account-activity, system notices.

``mandatory`` categories (security, billing) can't be fully muted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

BROADCAST = "broadcast"
TARGETED = "targeted"


@dataclass(frozen=True)
class NotificationCategory:
    key: str                                    # "alert.faults"
    label: str                                  # human label for the UI
    kind: str = BROADCAST                        # BROADCAST | TARGETED
    mandatory: bool = False                      # can't be fully turned off
    # BROADCAST only: role -> eligible.  None = every role eligible.
    audience: Callable[[str], bool] | None = None
    # Which source namespace this belongs to (derived from the key prefix)
    # — drives the UI section grouping in N4.
    @property
    def source(self) -> str:
        return self.key.split(".", 1)[0] if "." in self.key else self.key


_CATEGORIES: dict[str, NotificationCategory] = {}


def register_category(cat: NotificationCategory) -> NotificationCategory:
    """Register (last-wins, so a deployment can override)."""
    _CATEGORIES[cat.key] = cat
    return cat


def get_category(key: str) -> NotificationCategory | None:
    return _CATEGORIES.get(key)


def list_categories() -> list[NotificationCategory]:
    return list(_CATEGORIES.values())
