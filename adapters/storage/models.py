"""Data models and enums used across the database layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ─── Schema version ──────────────────────────────────────────────
SCHEMA_VERSION = 1


# ─── Roles ────────────────────────────────────────────────────────

class Role(str, Enum):
    """User roles — ordered from most to least privileged."""
    OWNER       = "owner"
    ADMIN       = "admin"
    FLEET       = "fleet"
    SAFETY      = "safety"
    DISPATCHER  = "dispatcher"
    # HR + ACCOUNTING are dedicated personas for the
    # people-management and money-management halves of the business.
    # They have their own subdomains (hr.4truck.us / accounting.4truck.us),
    # their own sidebar nav (interfaces/dashboard/src/shells/nav/),
    # and their own permission defaults (see capabilities/permissions/permissions.py).
    HR          = "hr"
    ACCOUNTING  = "accounting"
    DRIVER      = "driver"

    @classmethod
    def from_str(cls, s: str) -> "Role":
        s = s.strip().lower()
        if s == "fleet_manager":
            s = "fleet"
        for r in cls:
            if r.value == s:
                return r
        raise ValueError(f"Unknown role: {s}")


# ─── Data classes ─────────────────────────────────────────────────

@dataclass
class Account:
    id: int
    name: str
    slug: str
    tier: str
    is_active: bool
    created_at: str
    bot_token_encrypted: Optional[str] = None
    bot_username: str = ""
    webhook_secret: str = ""
    payroll_enabled: bool = False
    coaching_enabled: bool = False
    timezone: str = "America/New_York"
    alert_routing_mode: str = "single_group"
    # CSV of disabled module ids (Fleet/Dispatch/Safety/HR/Accounting);
    # empty = all modules on.  See capabilities/permissions/modules.py.
    disabled_modules: str = ""

@dataclass
class Company:
    id: int
    account_id: int
    code: str
    display_name: str
    samsara_api_key: str
    active_days: int
    is_active: bool
    created_at: str

@dataclass
class User:
    id: int
    # ``telegram_id`` is nullable for users who registered by email and
    # haven't linked Telegram yet.  Use ``id`` (the internal PK) for
    # anything that needs a guaranteed-present identifier; this field
    # is only set when the user has a real Telegram connection.
    telegram_id: Optional[int]
    account_id: int
    role: Role
    truck_num: Optional[str]   # for driver role
    alerts_on: bool
    is_active: bool
    created_at: str
    # Per-type alert preferences (all default ON when alerts_on is True)
    display_name: str = ""
    alert_faults: bool = True
    alert_health: bool = True
    alert_fuel: bool = True
    alert_geofence: bool = True
    ai_fault: bool = False      # Proactive AI on fault alerts
    ai_health: bool = False     # Proactive AI on health alerts
    ai_fuel: bool = False       # Proactive AI on fuel alerts
    alert_events: bool = True   # Safety event alerts
    ai_events: bool = False     # Proactive AI on event alerts
    alert_camera: bool = True   # Camera alerts
    alert_parking: bool = True  # Unsafe parking alerts
    ai_parking: bool = False    # Proactive AI on parking alerts
    # Per-user "🟢 RESOLVED" DM-receipt toggle.  Default off (opt-in)
    # — existing users don't get hit by a flood of receipts after the
    # migration lands; each user enables their own via the dashboard
    # "My Notifications" page (avatar menu).  Group-topic receipts are
    # governed by a parallel ``alert_routing.send_resolve_receipt``
    # column controlled by admins on the Account Settings page.
    alert_resolve_receipts: bool = False
    # Per-user Working Hours override (active alert-delivery window).
    # Despite the name, these are NOT "silence start/end" — they define
    # the ACTIVE window during which alerts deliver to this user.
    # Outside the window non-critical alerts queue for shift-start.
    # Both NULL → user inherits the role-level ``work_hours`` schedule
    # (or "no Working Hours" → alerts 24/7 if the role has none either).
    # Name kept ``quiet_*`` only because renaming the column requires a
    # backfill migration; the semantics are working-window everywhere.
    #
    # ADMIN-MANAGED ONLY (since migration 100): set via the Team
    # Management drawer Settings tab.  The user's own Profile page
    # shows these read-only and offers ``dnd_enabled`` below as the
    # only personal control over alert delivery.
    quiet_start: Optional[int] = None   # working window START hour (0-23)
    quiet_end: Optional[int] = None     # working window END hour   (0-23)
    # Personal DND toggle (migration 100).  When TRUE the user honours
    # the Working Hours schedule (per-user override OR role-level);
    # outside that window non-critical alerts queue for shift-start.
    # When FALSE the user opts out entirely — all non-critical alerts
    # deliver 24/7 regardless of schedule.  Critical-severity alerts
    # still bypass via ``bypasses_dnd`` at the pipeline call site.
    # Default TRUE so existing users behave exactly as they did
    # before this toggle was added.
    dnd_enabled: bool = True
    # FK to a ``work_hours`` row (migration 101).  Admin-managed: when
    # set, the user's active alert-delivery window is taken from THAT
    # named schedule, replacing the role-level lookup.  Lets operators
    # assign "Night Shift" / "Day Shift A" / etc. to specific people
    # instead of typing custom hours.  NULL → fall back to free-form
    # quiet_start/quiet_end (legacy override) → then role-level.
    assigned_work_hours_id: Optional[int] = None
    timezone: str = "America/New_York"
    language: str = "en"                # UI language (en/es/ru/uk/fr)
    last_shift_report: Optional[str] = None  # ISO date of last shift report
    email: Optional[str] = None         # For dashboard email+password login
    password_hash: Optional[str] = None # bcrypt hash
    # Samsara driver_id this user is linked to. Set by an admin so /coaching/me
    # and /payroll/me have a trustworthy identity binding instead of a
    # most-recent-event-by-truck heuristic. NULL ⇒ user has no fleet identity yet.
    samsara_driver_id: Optional[str] = None

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_admin_or_above(self) -> bool:
        return self.role in (Role.OWNER, Role.ADMIN)

    def wants_alert(self, alert_type: str) -> bool:
        """Check if user wants a specific alert type.

        alert_type: 'faults', 'health', 'fuel', 'geofence', or 'events'
        """
        if not self.alerts_on:
            return False
        return getattr(self, f"alert_{alert_type}", True)

    def is_in_quiet_hours(self) -> bool:
        """Returns True when the user is currently OUTSIDE their personal
        Working Hours override window — i.e. alerts should be DND-queued.

        ``quiet_start`` / ``quiet_end`` define the ACTIVE working window
        (when alerts SHOULD deliver).  This method returns True when
        the user's local time falls OUTSIDE that window, which the
        notification pipeline reads as "DND-active, queue this alert
        for shift-start."  The naming is historical and inverted from
        what the columns store; renaming would require a migration
        and a backfill, so the comment carries the contract instead.

        Returns False when no override is configured (both fields NULL)
        — the caller falls back to the role-level Working Hours.
        """
        if self.quiet_start is None or self.quiet_end is None:
            return False
        try:
            from zoneinfo import ZoneInfo
            user_tz = ZoneInfo(self.timezone)
            local_hour = datetime.now(timezone.utc).astimezone(user_tz).hour
            start, end = self.quiet_start, self.quiet_end
            # Determine if the user is INSIDE working hours
            if start <= end:
                in_working = start <= local_hour < end
            else:
                # Wraps midnight (e.g. 22:00 - 06:00) — common night-shift case.
                in_working = local_hour >= start or local_hour < end
            # DND-active = currently OUTSIDE the working window.
            return not in_working
        except Exception:
            return False

    @property
    def label(self) -> str:
        """Human-readable name for UI display.

        Order of preference:
          1. ``display_name`` (always set if known)
          2. ``email`` for email-only users without a Telegram link
          3. ``str(telegram_id)`` for Telegram users
          4. ``str(id)`` as the last-resort fallback
        """
        if self.display_name:
            return self.display_name
        if self.email:
            return self.email
        if self.telegram_id:
            return str(self.telegram_id)
        return str(self.id)

    @property
    def linked_label(self) -> str:
        """Clickable name linking to the user's Telegram profile."""
        name = self.display_name or str(self.telegram_id)
        return f"<a href='tg://user?id={self.telegram_id}'>{name}</a>"

@dataclass
class AuthorizedChat:
    id: int
    account_id: int
    chat_id: int             # Telegram group/channel ID (negative)
    chat_title: str
    added_by: int            # user.id who authorized
    is_active: bool
    created_at: str

# ─── Forum routing (Telegram group topics) ──────────────────────

# Canonical alert-type keys used by both alert_routing and the bot
# pipeline.  Adding a new alert type means: (a) appending to this
# tuple, (b) extending FORUM_TOPIC_SPEC in capabilities/alerting/
# forum_topics.py with name + icon, (c) emitting send_alert() calls
# with that key.  Order here mirrors the documented Option C topic
# list so dashboards and the bot setup wizard render in the same
# sequence.
ALERT_TYPE_KEYS: tuple[str, ...] = (
    "faults",
    "health",
    "fuel",
    "events",
    "camera",
    "parking",
    "geofence",
    "scorecard",
    "maintenance",
    "documents",
    "system",
)


@dataclass
class ForumGroup:
    """A Telegram supergroup with topics enabled, bound to one account.

    One account → at most one forum_group.  The bot must be an admin
    of this chat with the ``can_manage_topics`` right or every topic
    API call returns ``BAD_REQUEST: not enough rights``.
    """
    account_id: int
    chat_id: int               # Telegram chat_id (negative)
    chat_title: str
    is_forum_enabled: bool     # True if Telegram returned is_forum=true at setup
    setup_status: str          # 'pending' / 'provisioned' / 'partial' / 'error'
    last_setup_at: Optional[str]
    last_repair_at: Optional[str]
    created_by_user_id: int
    created_at: str
    updated_at: str


@dataclass
class AlertRoute:
    """Mapping from (account_id, alert_type) to a Telegram topic.

    ``message_thread_id`` is the Telegram topic id returned by
    ``createForumTopic``; ``chat_id`` always refers back to the
    forum_groups row for the same account (denormalised so the
    routing-hot-path send_alert() lookup is a single index hit).
    """
    id: int
    account_id: int
    alert_type: str            # one of ALERT_TYPE_KEYS
    chat_id: int
    message_thread_id: int
    topic_name_snapshot: str   # name at provisioning time (drift detection)
    icon_emoji: str            # default icon used when created
    is_active: bool            # soft-disable without delete
    # Per-topic "🟢 RESOLVED" auto-resolve receipt toggle.  When False
    # the auto-resolve pipeline still flips the underlying alert_history
    # row to resolved (dashboard monitoring view stays accurate) but
    # skips posting the receipt to this topic.  Defaults to True on
    # legacy rows via migration 079; admin flips via ForumRoutingSection.
    send_resolve_receipt: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PersonaGroup:
    """A flat Telegram group chat dedicated to one persona for one
    account, used when ``accounts.alert_routing_mode = 'per_persona_groups'``.

    Replaces the legacy single-forum-with-topics model: instead of one
    forum and a topic per alert_type, the account creates a separate
    group per persona (Dispatchers, Safety, Fleet, HR) and the owner/
    admin aggregate group.  ``alert_type → persona`` happens in
    ``capabilities.alerting.persona_mapping``.

    Composite key: (account_id, persona).  ``persona`` matches the
    persona slugs used by the dashboard shell (owner, admin, fleet,
    dispatcher, safety, hr, accounting, driver) — but in practice
    routing only writes to the aggregate-and-operational set:
    owner_admin, dispatcher, safety, fleet, hr.
    """
    id: int
    account_id: int
    persona: str
    chat_id: int              # Telegram chat_id (negative for groups)
    chat_title: str
    is_active: bool
    created_at: str
    updated_at: str


@dataclass
class Invite:
    id: int
    code: str
    account_id: int
    role: str
    truck_num: Optional[str]
    created_by: int          # user.id
    expires_at: str
    used_by: Optional[int]   # user.id who redeemed
    created_at: str
    # NULL on active invites; ISO-8601 UTC timestamp once revoked.
    # Mirrors the user_sessions.revoked_at pattern.  Default ``None``
    # so hydrating from a row that pre-dates migration 087 doesn't
    # AttributeError — defaults to active.
    revoked_at: Optional[str] = None
    # Email-channel state (migration 088).  ``sent_to_email`` arrives
    # decrypted via _row_to_invite; the storage layer is responsible
    # for round-tripping through infra.crypto.  All three default to
    # None / 0 so hydrating from a pre-migration row works.
    sent_to_email: Optional[str] = None
    email_sent_at: Optional[str] = None
    email_send_count: int = 0
    # Bounce / complaint state (migration 097).  All optional defaults
    # so a row that pre-dates 097 hydrates cleanly.  ``email_bounce_reason``
    # arrives decrypted via _row_to_invite (relay text routinely echoes
    # recipient address).
    resend_email_id: Optional[str] = None
    email_bounced_at: Optional[str] = None
    email_bounce_reason: Optional[str] = None
    email_bounce_type: Optional[str] = None
    email_soft_bounce_count: int = 0
    email_complained_at: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        exp = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) > exp

    @property
    def is_used(self) -> bool:
        return self.used_by is not None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def channel(self) -> str:
        """Derived 'link' | 'email' classifier — the canonical way to
        ask "how was this invite delivered to its recipient" without
        callers re-reading ``sent_to_email`` directly.  An invite is
        email-channel as soon as it's been TARGETED at a recipient,
        even if the SMTP send failed (email_sent_at IS NULL) —
        operator's intent was email, not link."""
        return "email" if self.sent_to_email else "link"

    @property
    def is_bounced(self) -> bool:
        """True when the email send hit a permanent bounce (hard
        bounce, soft-bounce-cap reached, or — set separately — a
        spam complaint).  Operator UX renders a 'Bounced' badge
        alongside the recipient address and replaces Resend with
        a 'Revoke & recreate' affordance.  Soft bounces below the
        cap (count<3) do NOT flip this — they self-clear or escalate
        on the next event."""
        return self.email_bounced_at is not None

    @property
    def is_complained(self) -> bool:
        """True when the recipient hit 'Report Spam'.  Distinct from
        ``is_bounced`` because the operator-facing remedy differs:
        bounced → likely typo, ask for corrected address; complained
        → recipient didn't want the invite, don't auto-revoke (silent
        destruction confuses operators) but flag prominently."""
        return self.email_complained_at is not None
