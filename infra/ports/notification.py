"""Notification port — abstract interface for alert delivery.

Re-exported from infra.ports for convenience.
"""

from infra.ports import NotificationSender

__all__ = ["NotificationSender"]
