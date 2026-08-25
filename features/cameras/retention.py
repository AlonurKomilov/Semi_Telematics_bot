"""Camera retention — the PHOTOS age out, the CHECK ROWS do not.

A camera check answers "is the dashcam working, clean, and pointed the
right way".  Its row (when, which truck, which camera, what verdict) is
a handful of bytes and is the actual history — that stays.  Its photo is
~77 KB and, at 158 checks a day, is the entire storage question.

WHY THE WINDOWS ARE SHORT
-------------------------
This is OPERATIONAL TELEMETRY, not evidence.  The photo's job is "the
lens on truck 103 is dirty — go clean it."  Once it's cleaned, last
quarter's picture of a dirty lens tells nobody anything.  That is a
different kind of data from an unsafe-parking snapshot (insurance and
litigation, 1095 days) or a driver's medical certificate (FMCSA, 1095
days), and it should not have silently inherited their window.

It nearly did: ``camera_checks`` was registered with no retention target
at all, so its photos would have grown unbounded.  Measured against the
1095-day default that works out to ~12.7 GB steady-state, for images
whose usefulness expires in weeks.

TWO WINDOWS, BECAUSE THE TWO KINDS DIFFER
-----------------------------------------
``OK``            30 days.  Worth keeping briefly: if an incident comes
                  up this week, the photo shows the camera was working.
                  Beyond that the row's verdict says the same thing for
                  0.01% of the bytes.
non-``OK``        180 days.  WARNING / ERROR / PROBLEM photos are what
                  you show a driver, and a season of them is a trend.

Steady state: ~1.4 GB, against 12.7 GB on the default and 8.0 GB if we
had instead simply refused to store passing checks — the WINDOW is the
lever here, not the status filter, because 63% of checks are already
non-OK.

Server-local only: ``prune_camera_images`` removes files from OUR disk
and never through ``ObjectStorage.delete``, which on the hybrid backend
would reach into the customer's Google Drive.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "cameras.images_ok", "Camera photos — passing checks", "tenant",
    lambda db, acct, days: db.prune_camera_images(
        acct, keep_days=days, ok_only=True,
    ),
))
register_need(RetentionNeed(
    "cameras", "cameras.images_ok", 30,
    "proof a camera was working recently; the check row keeps the verdict",
))

register_target(RetentionTarget(
    "cameras.images_problem", "Camera photos — warnings and faults", "tenant",
    lambda db, acct, days: db.prune_camera_images(
        acct, keep_days=days, ok_only=False,
    ),
))
register_need(RetentionNeed(
    "cameras", "cameras.images_problem", 180,
    "evidence shown to a driver, plus a season of obstruction trend",
))
