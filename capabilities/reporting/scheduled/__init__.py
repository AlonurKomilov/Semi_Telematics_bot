"""Scheduled Reports — the Reports service's sub-feature, in its own home.

A member subscribes to a report type (faults, fuel, health, efficiency,
camera check) on a schedule and a channel; the bot's hourly job renders
and delivers it.  The subscription API lives here; the delivery job is
the Telegram adapter (``interfaces/bot/scheduled_reports.py``); the
rows are ``digest_subscriptions`` (``adapters/storage/settings.py`` —
the table kept its old name to avoid a migration).

Why a home of its own: a permission on Reports must cover this too.
Today the door is ``can_digest``; when the services take matrix rows
that flag dies into the Reports service's view verb, and this folder
is the one place that needs to know.
"""

from .router import user_router  # noqa: F401
