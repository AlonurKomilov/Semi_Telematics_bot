"""Short-TTL in-memory response cache."""

from __future__ import annotations

import hashlib
import json
import time

_response_cache: dict[str, tuple[float, str]] = {}  # hash → (ts, text)
_CACHE_TTL = 90   # seconds
_CACHE_MAX = 50   # max entries


def _cache_key(question: str, snapshot_hash: str, model: str,
               account_id: int = 0, user_id: int = 0,
               language: str = "en") -> str:
    """Build a deterministic cache key.

    ``user_id`` is in the key because ``user_context`` (role, assigned
    vehicles) shapes the answer — a driver assigned to truck 231 and a
    dispatcher asking the same question must NOT share a cache entry.

    ``language`` is in the key because the same question/snapshot can
    produce wildly different responses per locale, and a stale
    English-cached reply being served to a Spanish-speaking user would
    miss the localization.
    """
    raw = (
        f"{account_id}:{user_id}:{model}:{language}:"
        f"{question.strip().lower()}:{snapshot_hash}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _snapshot_hash(data: dict | str | None) -> str:
    """Fast hash of the fleet snapshot."""
    if not data:
        return "empty"
    raw = json.dumps(data, separators=(',', ':'), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> str | None:
    """Return cached response text or None if miss/expired."""
    entry = _response_cache.get(key)
    if entry is None:
        return None
    ts, text = entry
    if time.time() - ts > _CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    return text


def _cache_put(key: str, text: str):
    """Store a response in the cache.

    Blank responses are never cached: a failed/empty generation must not
    poison the key — within the TTL every retry of the same question
    would silently serve the same empty answer with no model attempt,
    no telemetry row, and no error event ("No response received").
    """
    if not text or not text.strip():
        return
    if len(_response_cache) >= _CACHE_MAX:
        oldest_key = min(_response_cache, key=lambda k: _response_cache[k][0])
        _response_cache.pop(oldest_key, None)
    _response_cache[key] = (time.time(), text)
