"""Simple callback-query dispatcher with exact-match and prefix-based routing."""

from collections.abc import Callable
from typing import Any

_Handler = Callable[..., Any]


class CallbackRouter:
    """Registry-pattern callback dispatcher.

    Routes are checked in order: exact matches first, then prefix matches
    (first registered → first checked).
    """

    def __init__(self):
        self._exact: dict[str, _Handler] = {}
        self._prefixes: list[tuple[str, _Handler]] = []

    # ── Registration ─────────────────────────────────────────────

    def exact(self, pattern: str, handler):
        """Register an exact-match route."""
        self._exact[pattern] = handler

    def prefix(self, prefix: str, handler):
        """Register a prefix-match route.

        The handler receives ``(update, context)``; the full callback data is
        available via ``update.callback_query.data``.
        """
        self._prefixes.append((prefix, handler))

    # ── Dispatch ─────────────────────────────────────────────────

    async def dispatch(self, data: str, update, context) -> bool:
        """Try to dispatch *data* to a registered handler.

        Returns ``True`` if a handler was called, ``False`` otherwise.
        """
        handler = self._exact.get(data)
        if handler is not None:
            await handler(update, context)
            return True

        for pfx, handler in self._prefixes:
            if data.startswith(pfx):
                await handler(update, context)
                return True

        return False
