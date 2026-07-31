"""Driver onboarding — turning an approved applicant into a driver.

A SUB-FEATURE of the Drivers family (docs/FEATURES.md): its own home and
its own hub contribution, riding the Drivers surface rather than owning a
nav entry.  Sibling of ``features/drivers/documents/``.

Why it isn't part of Applications: hiring is where recruiting hands over
to driver administration.  The recruiter runs the pipeline and approves;
onboarding MINTS A USER, which is roster work — so it lives with the
roster and carries its own grant (``can_onboard_drivers``), narrower than
both ``can_invite`` and ``can_manage_drivers``.
"""
