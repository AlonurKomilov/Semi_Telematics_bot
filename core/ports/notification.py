"""Notification port — abstract interface for alert delivery.

Re-exported from core.ports for convenience.
"""

from core.ports import NotificationSender

__all__ = ["NotificationSender"]
