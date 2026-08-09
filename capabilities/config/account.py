"""Who owns each ``account_settings`` key.

THE PROBLEM THIS SOLVES
-----------------------
``account_settings`` is not one feature's private table.  It is a shared
key-value store that several peer features write into, and
``PUT /settings`` accepted ANY key with a single permission:

    key: str = Field(..., min_length=1, max_length=50)   # no allow-list
    user = Depends(require_permission("can_manage_config_all"))

``can_manage_account`` is the **General settings** feature's Manage action
— one row in the Administration band, described to the owner as
"timezone, bot + forum routing".  **Storage** and **Integrations** are
PEER features in that same band with their own Manage actions.  So one
feature's Manage was writing two sibling features' configuration, plus
the config-family members that correctly require ``can_manage_config_all``:

    object_storage.backend            → where every document is written
    object_storage.gdrive.*           → a live OAuth credential
    kpi_thresholds, scorecard rules   → Config · account-wide
    dqf.export_passphrase             → what protects applicants' SSNs

None of that is what an owner grants when they tick "General settings".

VIEW / MANAGE / CONFIG ARE THREE ACTIONS, NOT A GRADIENT
--------------------------------------------------------
The Permissions matrix gives every feature three columns, and they do not
overlap.  ``account_settings`` rows are the CONFIG column's account-wide
store — config.md's own table says so outright:

    Account | a feature's SHARED SETTINGS — one truth for everyone
            | account_settings key-value rows
            | can_manage_config_all

The first version of this registry preserved each writer's existing
permission — storage keys to ``can_manage_storage``, integrations keys to
``can_manage_integrations``, the rest to ``can_manage_account``.  That
made ownership visible, which was worth doing, and it ENCODED THE MIXING
rather than removing it: five permissions over one store, when the
architecture says one.

So every ``account_settings`` key is ``can_manage_config_all`` now.  A
feature's Manage keeps its real work — connecting Drive, retrying a sync,
running an import — and stops owning the VALUES a computation reads.  The
blast-radius rule is what settles it: "anything a computation reads is
account-wide, always."  The storage backend decides where every document
is written; provider precedence decides which value wins.  Those are
settings, not operations.

  CONFIG      the config family's account scope — ``can_manage_config_all``
              (docs/architecture/config.md).  The default, and now the
              only answer for an account_settings key.
  SYSTEM      platform infrastructure with no account-level action at all.
              AI model pinning is here: AI is a SERVICE, and services
              render "nothing to grant" because their access is DERIVED
              from feature grants.  There is no ``can_manage_ai`` and
              there should not be.
  PREFERENCE  per-user state.  config.md puts these in the preferences
              service, NOT here — ``user:*:ai_tier`` is in the wrong store
              and is declared so it can be found, not so it is blessed.

WHY A DECLARATION RATHER THAN MORE VALIDATION
---------------------------------------------
Same lesson as capabilities/object_storage/references.py: a pattern-match
over key names would miss whatever nobody anticipated, and the miss is
silent.  Every key is declared, an undeclared key is REFUSED rather than
written, and a drift test fails when code writes a key this file does not
know about.  Adding a setting then forces a decision about who owns it.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabilities.config._common import ACCOUNT_FLAG, Kind

__all__ = ["SettingOwner", "SETTING_OWNERS", "owner_for",
           "SYSTEM_ONLY", "SELF_ONLY", "ACCOUNT_FLAG", "Kind"]

# Sentinel owners for tiers that are not a feature permission.
SYSTEM_ONLY = "__system_owner__"
SELF_ONLY = "__self__"


@dataclass(frozen=True)
class SettingOwner:
    """One key (or key prefix) and the action allowed to write it."""

    key: str            # exact key, or a prefix ending in "*"
    permission: str     # a FeatureSet flag, or a sentinel above
    kind: Kind
    feature: str        # who owns it, for the error message and the docs
    note: str = ""

    def matches(self, candidate: str) -> bool:
        if self.key.endswith("*"):
            return candidate.startswith(self.key[:-1])
        return candidate == self.key


# Ordered most-specific first: the first match wins, so an exact key can
# override a prefix rule without the prefix having to know about it.
SETTING_OWNERS: tuple[SettingOwner, ...] = (
    # ── Storage (Administration band, its own feature) ──────────────
    SettingOwner(
        "object_storage.gdrive.refresh_token", "can_manage_config_all",
        "config", "storage",
        "A live OAuth credential.  It is a token in a key-value table, not "
        "a setting, and NOTHING should ever write it through PUT /settings "
        "— the OAuth callback stores it directly.  Declared anyway, and "
        "declared FIRST so the object_storage.* prefix cannot quietly "
        "grant it: an undeclared key is refused, but a key swept up by a "
        "broad prefix is accepted, and this is the one value in the store "
        "where that difference is a credential disclosure.",
    ),
    SettingOwner(
        "object_storage.*", "can_manage_config_all", "config", "storage",
        "Backend choice decides where every driver document and DQF lands.",
    ),
    SettingOwner(
        "storage.disk_quota_bytes", "can_manage_config_all", "config", "storage",
        "Per-account disk cap, overriding DEFAULT_DISK_QUOTA_BYTES.  Found "
        "by the drift test, not by hand — it is written from an adapter "
        "rather than a router, which is why it was missed in the manual "
        "sweep and why the test scans constants rather than call sites.",
    ),
    # ── Integrations (Administration band, its own feature) ─────────
    SettingOwner(
        "vehicle_field_precedence", "can_manage_config_all",
        "config", "integrations",
        "Which provider wins per vehicle field when Samsara and Datatruck "
        "disagree.",
    ),
    SettingOwner(
        "datatruck.*", "can_manage_config_all", "config", "integrations",
    ),
    SettingOwner(
        "datatruck_pay_estimate", "can_manage_config_all",
        "config", "integrations",
        "Opt-in tariff-estimated driver pay from Datatruck, default OFF "
        "until the math is validated against real settlements.  Read by "
        "adapters/storage/loads.py and written by nothing — found by the "
        "reader scan, not the writer scan, which is the pattern for an "
        "opt-in nobody wired a switch for.",
    ),
    # ── Config family, ACCOUNT scope ────────────────────────────────
    SettingOwner(
        "kpi_thresholds", "can_manage_config_all", "config", "kpi",
        "Grading thresholds — data-meaning config, so account-wide by the "
        "blast-radius rule.",
    ),
    SettingOwner(
        "scorecard_alert_*", "can_manage_config_all", "config", "scorecards",
        "Drop/floor thresholds that decide when a scorecard fires an "
        "alert.  Data-meaning config, so account-wide by the blast-radius "
        "rule — two roles must not disagree about when a driver is "
        "flagged.  jobs.py documents them as account-overridable but no "
        "code writes them: the only route is PUT /settings, which means an "
        "undeclared key made a documented override impossible.",
    ),
    SettingOwner(
        "dqf.*", "can_manage_config_all", "config", "applications",
        "The passphrase protecting applicants' SSNs in exported "
        "qualification files, and the one-shot export notice flag.",
    ),
    # ── Platform infrastructure — no account-level action exists ────
    SettingOwner(
        "ai_model", SYSTEM_ONLY, "system", "ai",
        "Pinning a model short-circuits the tier router's availability "
        "fallback.  AI is a SERVICE: nothing to grant at account level.",
    ),
    SettingOwner("ai_location", SYSTEM_ONLY, "system", "ai"),
    SettingOwner("ai_vision_model", SYSTEM_ONLY, "system", "ai"),
    SettingOwner("ai_vision_location", SYSTEM_ONLY, "system", "ai"),
    # ── Per-user preference — in the WRONG store, declared to be found ──
    SettingOwner(
        "user:*", SELF_ONLY, "preference", "ai",
        "Per-user tier/model choice.  docs/architecture/config.md puts "
        "per-user state in the preferences service, not account_settings; "
        "declared so the misplacement is visible rather than invisible.",
    ),
    SettingOwner(
        "ai_tier", "can_manage_config_all", "config", "ai",
        "The ACCOUNT's default tier when a user has not chosen one.  A "
        "default is an account operating choice, unlike the model itself.",
    ),
    # ── General settings' own keys ──────────────────────────────────
    SettingOwner(
        "forum_ai.*", "can_manage_config_all", "config", "settings",
        "Which alert types route to the AI forum topic.",
    ),
    # DEAD KEY, declared so a write is placed rather than refused, and
    # annotated so nobody wires it up believing it works.  The account
    # timezone lives on ``accounts.timezone`` (a COLUMN, migration 052)
    # and is written by PUT /admin/timezone.  Nothing reads this key;
    # GET /settings/config stopped returning it.  Rows may still exist on
    # accounts that set it years ago — they have no effect.  Safe to drop
    # from this table once those rows are purged.
    SettingOwner(
        "timezone", "can_manage_config_all", "config", "settings",
        "DEAD — superseded by the accounts.timezone column.",
    ),
    # The Settings page READS these six; before they were declared, my own
    # per-key enforcement refused five of them on write — a setting you
    # could see and not save.  The drift test could not catch it: it scans
    # write CALL SITES, and these are written through PUT /settings as a
    # runtime ``body.key`` it cannot resolve.  Hence
    # test_get_settings_allowlist_is_declared, which reads the GET list.
    SettingOwner("account_name", "can_manage_config_all", "config", "settings"),
    SettingOwner("alert_defaults", "can_manage_config_all", "config", "settings"),
    SettingOwner("language", "can_manage_config_all", "config", "settings"),
    SettingOwner("digest_hour", "can_manage_config_all", "config", "settings"),
    SettingOwner(
        "scorecard_default_subject", "can_manage_config_all", "config", "settings",
        "Default subject line for scorecard emails.  Account operating "
        "choice, not a scoring RULE — the rules themselves are scorecards' "
        "own rows behind can_manage_config_all.",
    ),
    SettingOwner("forum_*", "can_manage_config_all", "config", "settings"),
    SettingOwner("bot_*", "can_manage_config_all", "config", "settings"),
    SettingOwner("modules.*", "can_manage_config_all", "config", "settings"),
)


def owner_for(key: str) -> SettingOwner | None:
    """The declared owner of ``key``, or None when it is undeclared.

    None means REFUSE.  An undeclared key is one nobody has decided the
    ownership of, and defaulting it to the caller's permission is exactly
    how one feature came to write another's configuration.
    """
    for rule in SETTING_OWNERS:
        if rule.matches(key):
            return rule
    return None
