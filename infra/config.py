"""Shared configuration — non-bot-specific environment variables.

Bot-specific settings (TELEGRAM_TOKEN, WEBHOOK_*, WEBAPP_URL) stay in
bot/config.py.  Everything here is needed by the platform layer regardless
of whether a Telegram bot is running.
"""

import os

# ── Database ─────────────────────────────────────────────────────
#
# ``DATABASE_URL`` (required) is read directly by
# ``adapters/storage/core.py`` — see ``Database.initialize()`` for the
# boot-time check.  The legacy ``DATABASE_PATH`` env var that used to
# select a SQLite file was retired with the rest of the SQLite branch.

# Warehouse-first reads.  When ON, hot read endpoints (fleet/vehicles,
# safety/events, scorecards, faults, fuel) read from the local
# telemetry warehouse populated by the ingestor — Samsara is only
# called as a cold-start fallback when the warehouse is empty.
#
# Default ON: making the warehouse the single source of truth keeps
# dashboard p50 in the 10–120 ms range instead of the 800–2500 ms
# round-trips Samsara would otherwise impose. Override with
# ``WAREHOUSE_READS_ENABLED=0`` only when shadow-reading a brand-new
# environment whose warehouse hasn't been backfilled yet.
WAREHOUSE_READS_ENABLED = os.getenv("WAREHOUSE_READS_ENABLED", "1") not in ("0", "false", "False", "")

# Object storage backend.  "disk" (default) writes blobs under
# data/userdata/<bucket>/<key>.  "gdrive" routes uploads through the
# existing Google Drive adapter.  "hybrid" → disk-primary with async
# Drive sync.  Future: "s3", "gcs".
#
# OBJECT_STORE_ROOT lives under data/userdata/ so the per-tenant blob
# tree is visually separated from system/runtime files (which live in
# data/system/).  Legacy installations that still write under bare
# data/ can keep working by setting OBJECT_STORE_ROOT=data — the read
# path also tries both prefixes for backwards compat.
OBJECT_STORE_BACKEND = os.getenv("OBJECT_STORE_BACKEND", "disk")
OBJECT_STORE_ROOT = os.getenv("OBJECT_STORE_ROOT", "data/userdata")

# ── Samsara ──────────────────────────────────────────────────────

SAMSARA_BASE_URL = os.getenv("SAMSARA_BASE_URL", "https://api.samsara.com")
SAMSARA_DASHBOARD_URL = os.getenv(
    "SAMSARA_DASHBOARD_URL",
    SAMSARA_BASE_URL.replace("://api.", "://cloud.").rstrip("/"),
)

# ── Alert intervals & thresholds ─────────────────────────────────

ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "30"))
FUEL_THRESHOLD = int(os.getenv("FUEL_LOW_THRESHOLD_PERCENT", "20"))
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")


HEALTH_ALERT_COOLDOWN_HOURS = int(os.getenv("HEALTH_ALERT_COOLDOWN_HOURS", "8"))
# Hysteresis band that fuel must rise back above before a new low-fuel
# alert can fire.  Default 10 (was 5) so a truck oscillating around 25 %
# doesn't flip-flop into pinging operators every refuel-burn cycle.
FUEL_HYSTERESIS_PERCENT = int(os.getenv("FUEL_HYSTERESIS_PERCENT", "10"))
# Default raised 2h → 6h: a chronic SPN that re-fires every 2h was the
# single largest source of alert noise (one truck with stuck SPN 524133
# generated 5 160 alert messages over 52 days).  6h still surfaces a
# *new* fault within a shift while collapsing chronic noise 3×.
FAULT_ALERT_COOLDOWN_HOURS = int(os.getenv("FAULT_ALERT_COOLDOWN_HOURS", "6"))

# ── Chronic-fault suppression ─────────────────────────────────────
# Once an alert (vehicle + canonical-key) has fired this many times
# without resolving, stop sending per-cycle pushes for it.  The alert
# stays visible in the dashboard pending list and the daily digest
# until the underlying fault clears (auto-resolve) or an operator
# explicitly acknowledges + clears it.  Set to 0 to disable.
CHRONIC_FAULT_SUPPRESS_AFTER = int(os.getenv("CHRONIC_FAULT_SUPPRESS_AFTER", "10"))

# ── Re-escalation ─────────────────────────────────────────────────
# Hourly job pings subscribers about CRITICAL alerts that have been active
# for more than this many minutes without acknowledgment.  Tune via env.
REESCALATE_AFTER_MINUTES = int(os.getenv("REESCALATE_AFTER_MINUTES", "60"))
# Comma-separated alert_type values that qualify for re-escalation.
# (Only CRITICAL-tier checks should be in this list.)
REESCALATE_ALERT_TYPES = tuple(
    t.strip() for t in os.getenv(
        "REESCALATE_ALERT_TYPES", "fault,health"
    ).split(",") if t.strip()
)
# Maximum number of re-escalation pushes per logical alert (per recipient).
# After this many "🔴 UNACKNOWLEDGED ALERT" reminders, the job stops
# pinging — the alert is still visible in the dashboard pending list and
# auto-resolves when the underlying condition clears, so the operator's
# loop is "fix it / mute it / ack it" but Telegram stays quiet.
# Production data showed a single chronic alert generated 360+ reminders
# per recipient over 15 days (one per hour, no cap); 4 is enough to get
# attention without becoming spam.  Set 0 to disable re-escalation.
REESCALATE_MAX_ATTEMPTS = int(os.getenv("REESCALATE_MAX_ATTEMPTS", "4"))
# Exponential backoff between attempts (hours).  First reminder fires at
# REESCALATE_AFTER_MINUTES, then each successive reminder waits this many
# hours longer than the previous.  Default sequence: 1h → 4h → 12h → 24h.
REESCALATE_BACKOFF_HOURS = tuple(
    int(x) for x in os.getenv(
        "REESCALATE_BACKOFF_HOURS", "1,4,12,24"
    ).split(",") if x.strip().isdigit()
) or (1, 4, 12, 24)

RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "10"))

# ── Tenant resource quotas ────────────────────────────────────────
# Limits are designed for USA trucking companies:
#   - A 20-truck carrier already has 20+ drivers + dispatchers + safety officers.
#   - The natural scaling unit is FLEET SIZE (trucks), but we enforce on:
#       • users  — all bot users (drivers, dispatchers, admins combined)
#       • companies — Samsara API org integrations (divisions / subsidiaries)
#
# Tiers:
#   starter  — single small fleet, one Samsara org (trial / owner-ops / small carrier)
#   pro      — growing carrier or multiple divisions
#   enterprise — large carrier / fleet management company, unlimited
#
# 0 = unlimited.  All limits are env-overridable.
QUOTA_MAX_USERS: dict[str, int] = {
    "free":       int(os.getenv("QUOTA_FREE_MAX_USERS",       "0")),   # no user cap
    "starter":    int(os.getenv("QUOTA_STARTER_MAX_USERS",    "75")),  # ~50-truck carrier
    "pro":        int(os.getenv("QUOTA_PRO_MAX_USERS",        "500")), # large carrier
    "enterprise": 0,                                                     # unlimited
}
QUOTA_MAX_COMPANIES: dict[str, int] = {
    "free":       int(os.getenv("QUOTA_FREE_MAX_COMPANIES",       "1")),  # 1 Samsara org
    "starter":    int(os.getenv("QUOTA_STARTER_MAX_COMPANIES",    "3")),  # up to 3 divisions
    "pro":        int(os.getenv("QUOTA_PRO_MAX_COMPANIES",        "15")), # multi-division
    "enterprise": 0,                                                        # unlimited
}
# Max AI queries per user per day (0 = unlimited).
QUOTA_MAX_AI_QUERIES_PER_DAY: dict[str, int] = {
    "free":       int(os.getenv("QUOTA_FREE_MAX_AI_PER_DAY",       "20")),
    "starter":    int(os.getenv("QUOTA_STARTER_MAX_AI_PER_DAY",    "100")),
    "pro":        int(os.getenv("QUOTA_PRO_MAX_AI_PER_DAY",        "500")),
    "enterprise": 0,
}

# ── Vertex AI ────────────────────────────────────────────────────

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
VERTEX_AI_MODEL = os.getenv("VERTEX_AI_MODEL", "gemini-2.5-flash")

# ── Telegram / Web ───────────────────────────────────────────────
#
# Two distinct bot tokens, two distinct responsibilities.  Mixing them
# means the bot customers chat with is also the bot operators get
# ops alerts on, and rotating either for security logs out the wrong
# audience.  See docs/runbooks/system-dashboard-rollout.md for the
# operator-side picture.
#
#   TELEGRAM_BOT_TOKEN        — system / operator bot.  Used by
#                               infra.error_reporter for ops alerts,
#                               by the avatar fetcher in /admin/users,
#                               and as the Login Widget for
#                               system.4truck.us (operators only).
#                               No long-running daemon needed for this
#                               token — it's just a Telegram API client.
#
#   TELEGRAM_LOGIN_BOT_TOKEN  — customer-facing front-door bot.  Runs
#                               the long-running PTB daemon
#                               (interfaces/bot/app.py) that handles
#                               /start / /register / /join etc., and
#                               serves as the Login Widget fallback for
#                               customers whose account has no
#                               per-account bot configured.

# Primary env name is TELEGRAM_SYSTEM_BOT_TOKEN.  The legacy
# TELEGRAM_BOT_TOKEN name is still honored as a fallback so a .env
# that hasn't been renamed yet keeps booting; we log a one-time
# deprecation warning to nudge the rename.  Once every .env is updated
# the fallback (and this comment) can be deleted.
TELEGRAM_SYSTEM_BOT_TOKEN = (
    os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN", "")
)
if not os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN") and os.getenv("TELEGRAM_BOT_TOKEN"):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "TELEGRAM_BOT_TOKEN is deprecated — rename it to "
        "TELEGRAM_SYSTEM_BOT_TOKEN in .env.  The old name still works "
        "for now but will be removed in a future cleanup."
    )

TELEGRAM_LOGIN_BOT_TOKEN  = os.getenv("TELEGRAM_LOGIN_BOT_TOKEN", "")

# Legacy alias — kept so existing importers that still reference
# ``TELEGRAM_TOKEN`` don't break.  Always points at the SYSTEM bot
# token (the safer default for the error reporter, avatar fetcher, and
# system-side login).  Net-new code should import the explicit name.
TELEGRAM_TOKEN = TELEGRAM_SYSTEM_BOT_TOKEN

WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# ── Redis ────────────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0")

# ── Billing ──────────────────────────────────────────────────────
# BILLING_PROVIDER: 'stub' (default, no payment) | 'stripe'
# Stub provider is fully functional for development — it records DB rows
# and simulates upgrades without real payments.

BILLING_PROVIDER = os.getenv("BILLING_PROVIDER", "stub")

# Stripe keys — only needed when BILLING_PROVIDER=stripe
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_STARTER  = os.getenv("STRIPE_PRICE_STARTER", "")
STRIPE_PRICE_PRO      = os.getenv("STRIPE_PRICE_PRO", "")
# Per-extra-vehicle price ID, shared by both tiers (Stripe Dashboard:
# $2.99/mo recurring, "Quantity is variable" enabled).  When set, the
# checkout session attaches a second line item with quantity=0 and the
# ingest-driven sync nudges that quantity up/down as active vehicles
# change.  When unset (legacy installs) we fall back to single-line
# subscriptions and the dashboard hides the "extras" math.
STRIPE_PRICE_EXTRA_VEHICLE = os.getenv("STRIPE_PRICE_EXTRA_VEHICLE", "")

# Public-facing base URL used to build checkout success/cancel URLs
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://4truck.us")

# Hard-cap enforcement: set to '1' to start rejecting requests over quota.
# Default is soft-cap (log warning only) to avoid disrupting existing customers.
ENFORCE_HARD_CAP = os.getenv("ENFORCE_HARD_CAP", "0") == "1"

# Billing enforcement: when '1' the API returns HTTP 402 once a
# subscription is canceled/unpaid, or once past_due has been in effect
# longer than the grace period.  Defaults OFF so existing customers
# don't get cut off the moment this code ships; flip to '1' after the
# dashboard's past-due banner is live.
BILLING_ENFORCEMENT_ENABLED = os.getenv("BILLING_ENFORCEMENT_ENABLED", "0") == "1"
BILLING_GRACE_PERIOD_DAYS = int(os.getenv("BILLING_GRACE_PERIOD_DAYS", "7"))

# ── Error reporting ───────────────────────────────────────────────
# Telegram chat ID that receives built-in error reports.
# Set to a private group or your personal chat ID (use /chatid to find it).
# Leave empty to disable Telegram error notifications.
ERROR_REPORT_CHAT_ID = os.getenv("ERROR_REPORT_CHAT_ID", "")
