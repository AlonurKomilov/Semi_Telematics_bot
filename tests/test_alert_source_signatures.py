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

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "tests"}


def _modules_declaring_sources() -> list[str]:
    """Dotted module names for every file applying the decorator."""
    found: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
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
