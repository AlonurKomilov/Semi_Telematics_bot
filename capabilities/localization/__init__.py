"""Localization capability — i18n support with JSON locale files."""
from capabilities.localization.i18n import (  # noqa: F401
    t,
    set_lang,
    get_lang,
    reload_locales,
    SUPPORTED_LANGUAGES,
    LANGUAGE_NAMES,
    LANGUAGE_FLAGS,
    LOCALES_DIR,
)
