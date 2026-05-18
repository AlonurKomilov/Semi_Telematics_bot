"""Placeholder adapters for driver-future tables.

The tables (``driver_inspections``, ``driver_trainings``,
``driver_hos_status``) are created by migration 050 so reporting,
scorecards, and follow-on PRs can already join against them.  The
read/write paths land in their respective feature PRs:

  * **Inspections** — DOT pre/post-trip + annual + roadside.  Will
    likely pull data from Samsara's inspection report endpoint.
  * **Trainings** — defensive driving, hazmat, cargo securement.
    Will reuse the existing document store (``driver_documents``) for
    the certificate PDF; ``certificate_doc_id`` FK is already there.
  * **HOS status cache** — duty status, drive/on-duty seconds for the
    day, cycle/shift seconds remaining.  Refreshed by a scheduled job
    polling Samsara's HOS endpoint.

The mixins below are deliberately empty — they exist so the surface
area is owned by a real module rather than scattered across feature
PRs that each redefine their own bare-bones helpers.  Touching this
file in a feature PR keeps the diff localized.
"""

from __future__ import annotations


class DriverInspectionsMixin:
    """DOT inspection record adapter — empty stub.

    Schema available: ``driver_inspections``.
    """


class DriverTrainingsMixin:
    """Driver training records adapter — empty stub.

    Schema available: ``driver_trainings``.
    """


class DriverHosStatusMixin:
    """HOS / duty-status cache adapter — empty stub.

    Schema available: ``driver_hos_status`` (one row per driver).
    """
