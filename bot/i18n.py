"""Lightweight internationalisation for the bot.

Usage::

    from bot.i18n import t, set_lang

    # In handler — set once per request:
    set_lang(user.language)

    # Anywhere downstream — no ``lang`` argument needed:
    msg = t("welcome.title")
"""

import contextvars
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

SUPPORTED_LANGUAGES = ("en", "es", "ru", "uk", "fr", "so", "am", "uz", "pa")

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Español",
    "ru": "Русский",
    "uk": "Українська",
    "fr": "Français",
    "so": "Soomaali",
    "am": "አማርኛ",
    "uz": "O'zbek",
    "pa": "ਪੰਜਾਬੀ",
}

LANGUAGE_FLAGS = {
    "en": "🇺🇸",
    "es": "🇪🇸",
    "ru": "🇷🇺",
    "uk": "🇺🇦",
    "fr": "🇫🇷",
    "so": "🇸🇴",
    "am": "🇪🇹",
    "uz": "🇺🇿",
    "pa": "🇮🇳",
}

# ── Per-request language context ─────────────────────────────────
_current_lang: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_lang", default="en",
)


def set_lang(lang: str) -> None:
    """Set the language for the current async context (request)."""
    _current_lang.set(lang if lang in SUPPORTED_LANGUAGES else "en")


def get_lang() -> str:
    """Get the language for the current async context."""
    return _current_lang.get()


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _load_locale(lang: str) -> dict:
    """Load and cache a locale JSON file.  Returns {} on failure."""
    path = LOCALES_DIR / f"{lang}.json"
    if not path.is_file():
        logger.warning("Locale file not found: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def reload_locales() -> None:
    """Clear cached locales so next ``t()`` call reloads from disk."""
    _load_locale.cache_clear()


def _resolve(data: dict, key: str) -> str | None:
    """Resolve a dotted key like ``'menu.reports'`` in a nested dict."""
    parts = key.split(".")
    node = data
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node if isinstance(node, str) else None


def t(key: str, lang: str | None = None) -> str:
    """Return translated string for *key*.

    If *lang* is ``None``, uses the per-request context variable
    set by ``set_lang()``.

    Falls back to English, then returns the raw key so the UI is never blank.
    """
    if lang is None:
        lang = _current_lang.get()
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"

    # Try requested language
    if lang != "en":
        value = _resolve(_load_locale(lang), key)
        if value is not None:
            return value

    # Fallback to English
    value = _resolve(_load_locale("en"), key)
    if value is not None:
        return value

    # Last resort — return the key itself (should never happen in production)
    logger.warning("Missing i18n key: %s (lang=%s)", key, lang)
    return key
