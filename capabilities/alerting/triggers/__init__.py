"""Alert triggers — "tell me when DEF drops below 10% on my trucks".

A trigger is one person's own watch on one vehicle metric.  It is the
WHEN half of alerting, and it deliberately owns nothing of the WHETHER
half: delivery still runs through the notifications capability, with its
channel connections, category mutes, quiet hours and ledger untouched.

  catalog.py    the watchable vocabulary — a whitelist of metrics, each
                declaring its direction, re-arm band, freshness SLA,
                check cadence and whether it means anything with the
                engine off.  Adding a metric is a line here.
  models.py     the row, and what may be said about it
  evaluator.py  one sweep for every metric and every person
  router.py     /alerts/triggers — self-scoped CRUD

⚠️ Two things answer to the word "trigger" in this capability, and they
never meet in one file.  ``register_alert_source(trigger="interval")``
is APScheduler's schedule KIND, passed by eighteen feature checkers.
``AlertTrigger`` is this package's domain object.  Inside here, never
name a variable a bare ``trigger`` for the scheduler sense, and never
rename the registry's parameter — freeing that word would mean editing
those eighteen feature files for no gain.
"""

from capabilities.alerting.triggers import notification_category  # noqa: F401  registers alert.trigger
from capabilities.alerting.triggers.catalog import CATALOG, Metric, get_metric
from capabilities.alerting.triggers.models import (
    MAX_TRIGGERS_PER_USER, AlertTrigger, validate,
)

__all__ = [
    "CATALOG", "Metric", "get_metric",
    "AlertTrigger", "validate", "MAX_TRIGGERS_PER_USER",
]
