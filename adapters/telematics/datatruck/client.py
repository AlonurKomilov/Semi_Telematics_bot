"""Datatruck TMS HTTP client — per-account, per-tenant subdomain.

Datatruck's API lives at ``https://{company_subdomain}.datatruck.io/
api/v1/openapi/`` with a Token auth header.  The subdomain is the
operator's company slug on the Datatruck side (e.g. ``premier`` for
Premier Trucking Group).

Rate limit
----------
Datatruck enforces **20 requests/minute** globally per token.  This
client serialises calls through an asyncio.Semaphore so the per-
request gating happens BEFORE the HTTP round-trip even starts —
without that, a burst of concurrent reads (e.g. drivers + trucks +
trailers in parallel) would all fire and several would 429 back.

Pagination
----------
Datatruck caps results at **10 items per page**.  Endpoints return
``{count, next, previous, results: [...]}`` for ``/list/`` resources
and the same shape for ``/orders/``.  The ``paginated_get`` helper
walks every page until exhausted, respecting the rate limit.

Endpoints discovered live (10 Jun 2026 against premier.datatruck.io)
-------------------------------------------------------------------
  GET /api/v1/openapi/drivers/list/?page=N&page_size=10
  GET /api/v1/openapi/trucks/list/?page=N&page_size=10
  GET /api/v1/openapi/trailers/list/?page=N&page_size=10
  GET /api/v1/openapi/orders/?page=N&page_size=10
  GET /api/v1/openapi/work-orders/?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD

Auth header: ``Authorization: Token <api_key>``.

The docs at ``apidocs.datatruck.io`` are sparse on field-level shape
— field maps are derived at first sync from real responses and cached
in the integration row's ``credentials.field_map`` (future work).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


# Datatruck's documented quota.  Slightly conservative — 18/min keeps
# us out of edge-of-budget 429s when the upstream's window calculation
# rolls between our requests.
_RATE_LIMIT_PER_MIN = 18
_RATE_WINDOW_SEC = 60.0
_MAX_PAGE_SIZE = 10  # upstream-enforced cap


class DatatruckClient:
    """Per-account Datatruck API client.

    One instance per ``(account_id, integration)`` — held in the
    provider cache in ``infra.services``.  Closing the client closes
    the underlying aiohttp session; subsequent method calls will
    raise.

    The credentials shape Datatruck expects:
        {"company_subdomain": "premier", "api_token": "<hex>"}

    Both fields are required.  ``company_subdomain`` is the literal
    DNS label that prefixes ``.datatruck.io`` — operators see it in
    their Datatruck browser URL (e.g. ``premier.datatruck.io``).
    """

    def __init__(self, company_subdomain: str, api_token: str) -> None:
        if not company_subdomain or not api_token:
            raise ValueError(
                "DatatruckClient requires both company_subdomain and "
                "api_token — missing one or both",
            )
        # Defensive — strip any prefix the operator might have pasted.
        # ``premier`` is what we want; ``premier.datatruck.io`` and
        # ``https://premier.datatruck.io`` both reduce to ``premier``.
        sub = company_subdomain.strip().lower()
        sub = sub.removeprefix("https://").removeprefix("http://")
        # Order matters: trailing slash MUST be stripped before the
        # ".datatruck.io" suffix or "premier.datatruck.io/" leaves
        # the full host in place ("/" doesn't end with ".datatruck.io").
        sub = sub.removesuffix("/")
        sub = sub.removesuffix(".datatruck.io")
        # ``app`` is Datatruck's consolidated SPA login host — it's
        # the most likely wrong-paste from an operator who copied
        # their browser URL bar.  Give a specific error so they don't
        # waste minutes wondering why the token is "rejected" when
        # the real problem is the address.
        if sub == "app":
            raise ValueError(
                "'app.datatruck.io' is the shared Datatruck login page, "
                "not a company URL — paste the part that's unique to "
                "your company (e.g. 'premier')",
            )
        if not sub or "." in sub or "/" in sub:
            raise ValueError(
                f"company_subdomain {company_subdomain!r} is not a "
                "valid Datatruck slug (e.g. 'premier')",
            )
        self._subdomain = sub
        self._token = api_token
        self._base = f"https://{sub}.datatruck.io/api/v1/openapi/"
        self._session: Optional[aiohttp.ClientSession] = None
        # Rolling-window timestamp queue for client-side rate gating.
        # Holds the float-seconds timestamps of the last N requests;
        # before issuing a new one we pop expired entries and wait if
        # the queue is at the limit.
        self._gate_lock = asyncio.Lock()
        self._gate_window: list[float] = []

    @property
    def subdomain(self) -> str:
        return self._subdomain

    @property
    def base_url(self) -> str:
        return self._base

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Token {self._token}",
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _wait_for_quota(self) -> None:
        """Client-side rate gate.  Block until issuing one more
        request keeps us under the 18/min budget.

        Implementation: a rolling 60-second window of past request
        timestamps.  If the window is full, sleep until the oldest
        slot ages out.  Held under a lock so concurrent callers
        each see a coherent view of the budget.
        """
        async with self._gate_lock:
            now = time.monotonic()
            # Drop expired entries.
            cutoff = now - _RATE_WINDOW_SEC
            self._gate_window = [t for t in self._gate_window if t > cutoff]
            if len(self._gate_window) >= _RATE_LIMIT_PER_MIN:
                # Wait until the oldest entry falls out of the window.
                wait = self._gate_window[0] + _RATE_WINDOW_SEC - now
                if wait > 0:
                    logger.debug(
                        "datatruck client throttling — sleeping %.2fs "
                        "(window=%d/%d)",
                        wait, len(self._gate_window), _RATE_LIMIT_PER_MIN,
                    )
                    await asyncio.sleep(wait)
                # Refresh the window after the sleep.
                now = time.monotonic()
                cutoff = now - _RATE_WINDOW_SEC
                self._gate_window = [t for t in self._gate_window if t > cutoff]
            self._gate_window.append(now)

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Single GET — returns (status_code, json_body_or_text).

        Caller decides how to surface errors.  Rate-gated through
        ``_wait_for_quota`` so this is the ONE place rate-limiting
        happens.  Non-2xx responses don't raise — they return the
        status code plus the parsed body (or raw text on parse
        failure) so callers can build clean error messages.
        """
        return await self._get_url(
            self._base + path.lstrip("/"), params=params,
        )

    async def _get_url(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """GET an absolute URL — the body of ``_get`` plus the entry
        point for following paginated ``next`` links (which Datatruck
        returns as absolute URLs).  Guarded so we never follow a link
        off this client's own tenant host."""
        if not url.startswith(self._base):
            raise ValueError(
                f"refusing non-tenant URL {url!r} — pagination links "
                f"must stay under {self._base}",
            )
        await self._wait_for_quota()
        sess = await self._ensure_session()
        async with sess.get(url, params=params) as resp:
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                body: Any = await resp.json()
            else:
                body = await resp.text()
            return resp.status, body

    async def iter_pages(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 50,
    ):
        """Async generator over a paginated list endpoint.

        Yields each page's parsed body (the ``{count, next, previous,
        results}`` envelope).  Follows ``next`` links up to
        ``max_pages`` — the cap exists because the orders endpoint on
        a mature tenant holds 17k+ records (1,700+ pages at the
        10-item ceiling), which at 18 req/min is ~95 minutes; callers
        decide how much budget a sync run may burn and the sync
        status surfaces ``pages_done`` vs ``count`` so a capped run
        is visible, never silent.

        Raises ``RuntimeError`` on the first non-2xx page so callers
        fail loudly rather than persist a partial page silently.
        """
        merged = dict(params or {})
        merged.setdefault("page_size", _MAX_PAGE_SIZE)
        status, body = await self._get(path, params=merged)
        pages = 0
        while True:
            if status >= 400:
                raise RuntimeError(
                    f"datatruck {path} page {pages + 1} HTTP {status}: "
                    f"{str(body)[:200]}",
                )
            yield body
            pages += 1
            next_url = body.get("next") if isinstance(body, dict) else None
            if not next_url or pages >= max_pages:
                return
            status, body = await self._get_url(next_url)

    async def test_connection(self) -> tuple[bool, str, dict[str, Any] | None]:
        """Cheapest possible probe — ``GET /drivers/list/?page_size=1``.

        Returns ``(ok, message, sample_meta)`` where ``sample_meta``
        carries ``{count: N}`` on success so the dashboard can render
        "60 drivers reachable".  Three failure modes mapped explicitly:

          * 401 / 403 — token rejected
          * 404 — wrong subdomain (host responded; resource missing)
          * connection error / timeout — bad host or network
        """
        try:
            status, body = await self._get(
                "drivers/list/", params={"page_size": 1},
            )
        except asyncio.TimeoutError:
            return False, "timeout after 15s — check subdomain", None
        except aiohttp.ClientError as e:
            return False, (
                f"connection error: {type(e).__name__} — "
                "check subdomain"
            ), None
        if status in (401, 403):
            return False, (
                f"credentials rejected (HTTP {status}) — "
                "check api token"
            ), None
        if status == 404:
            return False, (
                f"host responded but resource not found (HTTP 404) — "
                "check company subdomain"
            ), None
        if status >= 400:
            return False, f"HTTP {status}", None
        # Success — extract the count if the response is paginated.
        count = None
        if isinstance(body, dict):
            count = body.get("count")
        return True, "reachable", {"count": count} if count is not None else {}

    async def list_drivers(self, *, page_size: int = _MAX_PAGE_SIZE) -> dict:
        """Single-page driver list.  Caller uses ``next`` for paging."""
        page_size = min(page_size, _MAX_PAGE_SIZE)
        status, body = await self._get(
            "drivers/list/", params={"page_size": page_size},
        )
        if status >= 400:
            raise RuntimeError(f"datatruck drivers/list HTTP {status}: {body}")
        return body

    async def list_trucks(self, *, page_size: int = _MAX_PAGE_SIZE) -> dict:
        page_size = min(page_size, _MAX_PAGE_SIZE)
        status, body = await self._get(
            "trucks/list/", params={"page_size": page_size},
        )
        if status >= 400:
            raise RuntimeError(f"datatruck trucks/list HTTP {status}: {body}")
        return body

    async def list_trailers(self, *, page_size: int = _MAX_PAGE_SIZE) -> dict:
        page_size = min(page_size, _MAX_PAGE_SIZE)
        status, body = await self._get(
            "trailers/list/", params={"page_size": page_size},
        )
        if status >= 400:
            raise RuntimeError(f"datatruck trailers/list HTTP {status}: {body}")
        return body

    async def list_orders(self, *, page_size: int = _MAX_PAGE_SIZE) -> dict:
        page_size = min(page_size, _MAX_PAGE_SIZE)
        status, body = await self._get(
            "orders/", params={"page_size": page_size},
        )
        if status >= 400:
            raise RuntimeError(f"datatruck orders HTTP {status}: {body}")
        return body

    async def list_work_orders(
        self,
        *,
        from_date: str,
        to_date: str,
        page_size: int = _MAX_PAGE_SIZE,
    ) -> dict:
        """Work-orders requires a date range.  Dates are ISO ``YYYY-MM-DD``."""
        page_size = min(page_size, _MAX_PAGE_SIZE)
        status, body = await self._get(
            "work-orders/",
            params={
                "page_size": page_size,
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        if status >= 400:
            raise RuntimeError(
                f"datatruck work-orders HTTP {status}: {body}",
            )
        return body
