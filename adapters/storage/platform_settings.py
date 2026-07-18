"""Platform settings — console-flipped launch gates and toggles.

Why this exists: launch gates used to be .env-only (the operator had
to SSH + edit + restart).  The owner runs the platform from
system.4truck.us, so gates become DB-backed switches there.  Env vars
remain honored as EMERGENCY OVERRIDES (a truthy env forces ON without
a DB round-trip — useful in tests and incident response) and for
back-compat with already-deployed .env files.

This mixin is also the layer-boundary fix for the old "deliberate
twin" hack: features/ may not import capabilities.platform.*, so the
MARKET_INTEL_ENABLED reader used to be duplicated in both layers.  A
method on the shared ``Database`` is the one legal home both sides
can call — the twin is gone.

Reads are cached per-process for 60s (same reasoning as the
permissions cache: cheap staleness beats a DB hit per request).
"""

from __future__ import annotations

import os
import time

_SETTINGS_TTL_S = 60.0
_settings_cache: dict[str, tuple[float, str]] = {}

MARKET_INTEL_KEY = "market_intel_enabled"
MARKET_INTEL_ENV = "MARKET_INTEL_ENABLED"
_TRUTHY = ("1", "true", "TRUE", "yes")


class PlatformSettingsMixin:

    async def get_platform_setting(self, key: str, default: str = "") -> str:
        cached = _settings_cache.get(key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        cur = await self._db.execute(
            "SELECT value FROM platform_settings WHERE key = ?", (key,),
        )
        row = await cur.fetchone()
        value = dict(row)["value"] if row else default
        _settings_cache[key] = (now + _SETTINGS_TTL_S, value)
        return value

    async def set_platform_setting(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO platform_settings (key, value, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            " updated_at = EXCLUDED.updated_at",
            (key, value, self._now()),
        )
        await self._db.commit()
        _settings_cache.pop(key, None)  # this process sees it instantly

    async def market_intel_enabled(self) -> bool:
        """THE market-intel launch gate — console switch first, env as
        emergency override.  Every reader (feature endpoints, nightly
        job) resolves through here; there is no other source."""
        if os.getenv(MARKET_INTEL_ENV, "").strip() in _TRUTHY:
            return True
        return await self.get_platform_setting(MARKET_INTEL_KEY, "0") == "1"
