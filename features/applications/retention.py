"""Applications' data-retention contribution — apply-form drafts.

Drafts are pre-consent PII held only so an applicant can resume an
unfinished form; they are deleted on submit, and anything idle longer than
the window below is purged by the nightly retention run.  Platform scope:
one sweep covers every account (the cutoff is identical for all).
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "applications.drafts", "Apply-form drafts (save & resume)", "platform",
    lambda db, acct, days: db.prune_application_drafts(days_keep=days),
))

register_need(RetentionNeed(
    "applications", "applications.drafts", 14,
    "resume window for unfinished public applications (pre-consent PII — keep short)",
))
