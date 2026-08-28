"""Alert-source registry — the Alerts hub's extension point.

Every periodic alert source (a feature's contribution to the Alerts hub)
self-registers here with its APScheduler schedule, and
``interfaces/bot/scheduler.py`` registers jobs by looping the registry
instead of hardcoding one ``add_job`` block per source.  Adding a new
alert source = decorate its ``async def check_x(app)`` entrypoint and
import the module from the scheduler's source-import block — no
scheduler edits.

Mirrors the AI tools registry pattern (``capabilities/ai/tools/registry.py``):
decorator + module-import side effect; the importing site controls WHICH
sources load, the source controls HOW it runs.

The ``key`` doubles as the APScheduler job id — ids are load-bearing
(replace_existing semantics, ops dashboards), so they must stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine


@dataclass(frozen=True)
class AlertSource:
    key: str                                       # APScheduler job id
    fn: Callable[..., Coroutine[Any, Any, None]]   # async def check_x(app)
    trigger: str                                   # "interval" | "cron"
    schedule: dict[str, Any]                       # trigger kwargs (minutes=, minute=, hours=…)


# test-safe: filled by @register_alert_source across many feature modules at
# import.  Restoring a snapshot would strip whatever a module first
# imported inside a test had just registered.
_ALERT_SOURCES: dict[str, AlertSource] = {}


def register_alert_source(key: str, *, trigger: str, **schedule: Any):
    """Decorator: register an ``async def check_x(app)`` as an alert source.

    ``trigger`` + ``**schedule`` are passed straight to
    ``scheduler.add_job`` — keep them identical to the job's historical
    registration so APScheduler behavior is unchanged.
    """
    def decorator(fn: Callable[..., Coroutine[Any, Any, None]]):
        if key in _ALERT_SOURCES:
            raise ValueError(f"Duplicate alert-source key: {key!r}")
        _ALERT_SOURCES[key] = AlertSource(
            key=key, fn=fn, trigger=trigger, schedule=dict(schedule),
        )
        return fn
    return decorator


def alert_sources() -> tuple[AlertSource, ...]:
    """All registered sources, in registration order."""
    return tuple(_ALERT_SOURCES.values())
