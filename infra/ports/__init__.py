"""Notification port — abstract interface for alert delivery.

Defines the Protocol that any notification backend (Telegram, email,
webhook, etc.) must implement. The alerting pipeline calls this
interface instead of Telegram directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotificationSender(Protocol):
    """Interface for sending alert notifications to a user."""

    async def send_alert_message(
        self,
        *,
        chat_id: int,
        text: str,
        keyboard: object | None = None,
        photo_bytes: bytes | None = None,
        video_url: str = "",
    ) -> int:
        """Send an alert message and return the message ID.

        Parameters
        ----------
        chat_id : int
            Recipient identifier (Telegram chat ID, etc.).
        text : str
            Alert body (may contain HTML markup).
        keyboard : object | None
            Platform-specific reply markup, if any.
        photo_bytes : bytes | None
            Optional image attachment.
        video_url : str
            Optional video URL to embed.

        Returns
        -------
        int
            The sent message's ID (for later deletion / update).
        """
        ...

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a previously sent message. Returns True on success."""
        ...
