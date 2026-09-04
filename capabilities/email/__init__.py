"""Notification transports for out-of-band alerts.

Currently only ``email`` is implemented (stdlib smtplib, no external SDK).
SMS transport is a future extension — pluggable behind the same shape.
"""

from .smtp import send_email, send_email_detailed, is_email_configured  # noqa: F401
