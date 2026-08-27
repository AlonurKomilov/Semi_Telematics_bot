"""Every registered alert source must be callable the way the scheduler
calls it — CI-enforced, because getting this wrong crashes the bot at
boot rather than at the failing job's first run.

``interfaces/bot/scheduler.py`` registers each source with a fixed
shape::

    for src in alert_sources():
        scheduler.add_job(src.fn, src.trigger, args=[app], id=src.key, ...)

``add_job`` validates the signature eagerly via
``apscheduler.util.check_callable_args``.  A source whose function does
not accept exactly that one positional argument therefore raises
``ValueError`` inside ``register_all`` — which runs during startup,
before the bot ever reaches polling.  systemd's ``Restart=always`` then
masks it as a boot flap.

This is not hypothetical.  A decorator separated from its function by a
blank line::

    @register_alert_source("critical_reescalate", trigger="interval", hours=1)

    async def _spine_remind(tenant, account_id, history_id, *, ...):

still binds to the ``def`` that follows it — Python skips blank lines
between a decorator and its target — so the registry pointed at a
helper taking three positional arguments instead of the hourly job.
Result: ``ValueError: The following arguments have not been supplied:
account_id, history_id`` on every boot that reached this loop, logged 17
times before anyone noticed, because the only health check at the time
polled the API rather than the bot.

Sources are discovered by scanning for the decorator rather than by
importing a hand-maintained list, so a new alert source is covered the
moment it is written.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from apscheduler.util import check_callable_args

from capabilities.alerting.registry import alert_sources
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted
from tests._repo import scanned  # a guard that scans nothing must fail

REPO_ROOT = _REPO

SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "tests"}


def _modules_declaring_sources() -> list[str]:
    """Dotted module names for every file applying the decorator."""
    found: list[str] = []
    for path in scanned(REPO_ROOT.rglob("*.py"), "repo sources"):
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "@register_alert_source" not in text:
            continue
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        found.append(".".join(rel.parts))
    return sorted(found)


@pytest.fixture(scope="module")
def registered_sources():
    """Import every declaring module so the registry is fully populated.

    Importing is what registers — the decorator runs at import time — so
    the registry is empty until this happens.
    """
    modules = _modules_declaring_sources()
    assert modules, "no modules apply @register_alert_source — scan is broken"
    for mod in modules:
        importlib.import_module(mod)
    sources = alert_sources()
    assert sources, "modules imported but registry is empty"
    return sources


def test_every_alert_source_accepts_the_scheduler_call(registered_sources):
    """``add_job(src.fn, args=[app])`` must validate for every source."""
    app_sentinel = object()  # stands in for the Telegram Application
    failures = []
    for src in registered_sources:
        try:
            check_callable_args(src.fn, [app_sentinel], {})
        except ValueError as exc:
            failures.append(f"{src.key} -> {src.fn.__module__}.{src.fn.__name__}: {exc}")

    assert not failures, (
        "These alert sources cannot be called as the scheduler calls them, "
        "so registering them raises at bot startup:\n  "
        + "\n  ".join(failures)
        + "\n\nUsual cause: the @register_alert_source decorator is attached "
        "to the wrong function — check for a blank line between the "
        "decorator and its intended def."
    )


def test_critical_reescalate_binds_to_the_hourly_job(registered_sources):
    """Regression guard for the specific mis-binding described above."""
    by_key = {src.key: src for src in registered_sources}
    assert "critical_reescalate" in by_key, "critical_reescalate is not registered"
    assert by_key["critical_reescalate"].fn.__name__ == "re_escalate_critical_alerts", (
        "critical_reescalate is bound to "
        f"{by_key['critical_reescalate'].fn.__name__!r}, not the hourly "
        "re-escalation job — a decorator has drifted onto a neighbouring def."
    )


# ── Every alert source declares where it stands on retired trucks ────
#
# The class of bug this catches is the one that got past a whole audit:
# a source that never touches the `vehicles` table at all. Some ask the
# provider directly (cameras, safety events); most read
# `warehouse.vehicle_state_*` by account_id. Neither shape is findable
# by grepping SQL for `is_active`, which is exactly why an archived
# truck kept DMing alerts, assigning PTI inspections to drivers, and
# spending AI vision budget for weeks.
#
# So the guard is a DECLARATION, not a scan. Adding a source means
# saying what it does about a truck the customer retired — and the only
# way to say nothing is to fail this test.

#: How each registered source treats a vehicle that has left the fleet.
#:
#:   filters  it excludes retired vehicles itself
#:   gated    it reads warehouse state, which the ingest gate stops
#:            writing for an operator-archived truck, so it goes quiet
#:            on its own (capabilities/integrations/samsara/sync.py)
#:   n/a      not vehicle-scoped
#:   open     KNOWN LEAK, not yet fixed — see the ratchet below
ARCHIVED_STANCE: dict[str, str] = {
    # Vehicle-scoped, filters for itself.
    "alert_triggers_sweep": "filters",   # _target_scope + vehicle_gate
    "camera_check": "filters",           # gather_snapshots drops by ref
    "events_check": "filters",           # safety events drop by ref
    # Vehicle-scoped, silenced by the ingest gate: with no fresh live
    # row, each of these closes on its own staleness bar.
    "fault_check": "gated",
    "fuel_check": "gated",
    "health_check": "gated",
    "geofence_check": "gated",
    "parking_check": "gated",
    "maintenance_check": "gated",
    "maintenance_engine_hours_check": "gated",
    "maintenance_mileage_check": "gated",
    "maintenance_warning_check": "gated",
    # Not about vehicles.
    "dnd_delivery": "n/a",
    "driver_doc_expiry_check": "n/a",
    "driver_onboarding_stale_check": "n/a",
    "driver_samsara_sync": "n/a",
    # ── Known leaks, deliberately recorded rather than forgotten ──
    # Re-notifies unacknowledged alert_history rows; candidates come
    # from alert_history alone and the registry is never consulted, so
    # an alert raised before a truck was archived keeps escalating
    # after it.
    "critical_reescalate": "open",
    # Compares nightly scorecard snapshots. Its roster is historical,
    # so the ingest gate cannot quiet it: last month's score for a truck
    # you ran last month is legitimate data. The ALERT should skip
    # retired trucks while the page keeps scoring them — two different
    # fixes, and an owner decision about which.
    "scorecard_drop_alerts": "open",
}

#: Exactly the sources still leaking. A ratchet, like the lint budget:
#: fixing one means removing it here, and a NEW leak cannot be added
#: quietly.
KNOWN_OPEN = {"critical_reescalate", "scorecard_drop_alerts"}


def test_every_alert_source_declares_its_stance_on_retired_trucks(
    registered_sources,
):
    """A new source must say what it does about an archived vehicle.

    Undeclared fails. That is the whole mechanism: the failure arrives
    at the moment someone adds a source, when they still know the
    answer, instead of months later as a customer asking why a truck
    they sold is texting them.
    """
    declared = set(ARCHIVED_STANCE)
    registered = {s.key for s in registered_sources}

    undeclared = registered - declared
    assert not undeclared, (
        "alert source(s) with no declared stance on retired vehicles: "
        f"{sorted(undeclared)}. Add each to ARCHIVED_STANCE — 'filters' "
        "if it excludes them itself, 'gated' if it reads warehouse state "
        "the ingest gate already stops, 'n/a' if it is not about "
        "vehicles, or 'open' with a comment saying why it still leaks."
    )

    stale = declared - registered
    assert not stale, (
        f"ARCHIVED_STANCE names source(s) that no longer exist: "
        f"{sorted(stale)} — a guard describing a tree that moved on is a "
        "guard nobody can trust."
    )

    bad = {k: v for k, v in ARCHIVED_STANCE.items()
           if v not in {"filters", "gated", "n/a", "open"}}
    assert not bad, f"unknown stance value(s): {bad}"


def test_the_known_leaks_are_exactly_the_ones_we_admit_to(registered_sources):
    """A ratchet, not a permanent excuse.

    Fixing a source means deleting it from KNOWN_OPEN, and a NEW leak
    cannot be introduced without editing this line — which is the point
    where someone has to justify it out loud.
    """
    open_now = {k for k, v in ARCHIVED_STANCE.items() if v == "open"}
    assert open_now == KNOWN_OPEN, (
        f"the set of leaking alert sources changed: {sorted(open_now)} "
        f"vs the admitted {sorted(KNOWN_OPEN)}"
    )
