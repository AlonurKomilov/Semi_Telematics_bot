"""Who may receive which scheduled report — the one rule, three readers.

A schedule is two grants at once: the Reports service (``can_view_reports``)
and the report TYPE's own view verb (``ReportSpec.permission`` — faults →
``can_view_faults``, camera → ``can_view_cameras`` …).  Until 2026-09-07
only the service verb was asked, at subscribe time; a role that could not
open Cameras could still subscribe to the camera check and receive it
every morning.  The API asks this rule when a schedule is saved, the bot
wizard asks it before offering a type, and the delivery job asks it for
every row before sending — a grant taken away after the subscription
stops the report and deactivates the row.
"""

from __future__ import annotations

from capabilities.reporting.registry import get_report


def report_permission(report_type: str) -> str | None:
    """The type's own view verb, or None for an unknown type."""
    spec = get_report(report_type)
    return spec.permission if spec else None


def perms_allow(perms, report_type: str) -> bool:
    """Both grants, against a resolved FeatureSet."""
    flag = report_permission(report_type)
    if flag is None:
        return False
    return bool(getattr(perms, "can_view_reports", False)) and bool(getattr(perms, flag, False))


async def may_receive(
    account_id: int, role, report_type: str, *,
    is_manager: bool = False, is_primary_owner: bool = False,
) -> bool:
    """The delivery job's question — resolved for THIS account and the
    member's tier, never the role's seed."""
    from capabilities.permissions.roles import get_user_permissions
    perms = await get_user_permissions(
        role, account_id, is_manager=is_manager, is_primary_owner=is_primary_owner,
    )
    return perms_allow(perms, report_type)
