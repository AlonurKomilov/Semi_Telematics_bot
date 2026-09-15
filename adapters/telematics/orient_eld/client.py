"""ORIENT ELD HTTP client — one company's key, one company's trucks.

ORIENT ELD is an electronic logging device platform.  Its public API
lives at ``publicapi.mgkeld.com`` (the vendor's own docs are a Notion
page that links there), authenticates with an ``x-api-key`` header, and
issues one key PER COMPANY — which is why the fan-out below mirrors the
Samsara shape rather than the single-token Datatruck one.

What this vendor does and does not report
-----------------------------------------
``/api/logs/tracking`` is the whole duty-status surface: it answers
"what is this driver doing right now, since when, and where".  It does
NOT report a single hours-of-service COUNTDOWN — no drive-remaining, no
shift-remaining, no cycle-remaining, no break-due-in.  That is a fact
about the vendor, confirmed against both their OpenAPI document and a
live 22-driver response, and the provider layer above must state it
rather than let four empty columns imply zeroes.

Three quirks worth the reader's time, all verified against live data
-------------------------------------------------------------------
1. ``status_activation_time_utc`` carries NO ``Z`` while ``datetime_utc``
   in the same payload does.  Both are UTC; only one says so.  Parsing
   the first as naive local time shifts a duty status by the reader's
   offset — four hours, on the account this was verified against.
   :func:`utc_iso` forces the zone on every ``*_utc`` field.

2. ``datetime_utc`` is NOT the UTC rendering of ``datetime``.  They are
   the same wall clock only by coincidence: ``datetime`` pairs with
   ``status_activation_time`` (the local status clock) while
   ``datetime_utc`` equals ``location_datetime_utc`` — the TELEMETRY
   sample time.  On a live row those differed by 84 seconds.  Anything
   asking "how old is this reading" wants the telemetry one.

3. ``status_duration`` is derived upstream, not observed: across a
   whole page every row's ``status_activation_time_utc + status_duration``
   landed on the same instant — the server's clock at request time.  So
   the activation timestamp is the fact and the duration is a
   convenience; storing the duration would bake OUR fetch time into a
   column that reads like the vendor's.

Rate limits
-----------
The vendor publishes them per ENDPOINT GROUP, not per account, and the
groups are wildly different — 100/minute for the directory reads but
one call per six seconds for tracking.  A single budget would either
throttle the cheap reads pointlessly or blow through the expensive one,
so the gate below is per group.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


ORIENT_BASE_URL = "https://publicapi.mgkeld.com"

#: How we introduce ourselves to ORIENT ELD.
#:
#: aiohttp's default is ``Python/3.12 aiohttp/3.x``, which says nothing
#: about who is calling.  This is not a courtesy: ORIENT publishes
#: per-endpoint rate limits and this key is a live production
#: credential, so when a call of ours misbehaves — a tight loop, a
#: retry storm, a limit we misread — the vendor's logs should name the
#: product doing it rather than "some Python script".
#:
#: Picked up from the live-map session, whose Overpass client was being
#: answered with HTTP 406 BEFORE the query was read, purely for having
#: no User-Agent, while the client logged it as "the source is not
#: answering".  ORIENT does not enforce it today (verified: it answers
#: the default agent fine), which is exactly why it costs nothing to
#: send and would cost a confusing outage not to.
USER_AGENT = "4truck-telematics/1.0 (+https://4truck.us)"

# Upstream's own cap on the ``size`` query parameter.  Asking for more
# is a 422, not a silent clamp.
MAX_PAGE_SIZE = 100

# A hard stop on pagination.  At the tracking group's one-call-per-six-
# seconds budget, 20 pages is two minutes of wall clock for a 2,000-
# driver company — beyond that a caller is better served by an error it
# can see than by a job that quietly runs long.
MAX_PAGES = 20


# Endpoint group → (calls, per_seconds), transcribed from the vendor's
# published limits.  Deliberately one notch under each published figure
# where rounding allows: an upstream window that rolls between our
# requests turns an exactly-at-budget client into a 429.
_GROUPS: dict[str, tuple[int, float]] = {
    # /api/vehicles/locations, /api/vehicles/{id}/locations — 2/6s
    "locations": (2, 6.0),
    # /api/companies/info, /api/drivers, /api/vehicles — 100/min
    "directory": (90, 60.0),
    # /api/logs/tracking, /api/reports/ifta — 1/6s
    "tracking":  (1, 6.0),
}


def _group_for(path: str) -> str:
    """Which published budget a path spends from.

    Defaults to the TIGHTEST group rather than the loosest: an endpoint
    this map has not heard of is one the vendor added after this file
    was written, and guessing generous on an unknown limit is how a key
    gets rate-limited for everyone sharing it.
    """
    p = path.lower()
    if "locations" in p:
        return "locations"
    if p.startswith("/api/companies") or p.startswith("/api/drivers") \
            or p.rstrip("/").endswith("/api/vehicles"):
        return "directory"
    return "tracking"


def utc_iso(raw: Any) -> str:
    """One of the vendor's ``*_utc`` strings as an explicit UTC ISO stamp.

    The vendor is inconsistent about the zone suffix within a single
    payload — see quirk 1 in the module docstring — so a value that
    parses as naive is STAMPED as UTC rather than left for whatever
    reads it next to interpret against the local clock.  The field name
    is the contract here; a bare ``datetime`` (no ``_utc``) must never
    be passed to this function.

    Returns ``""`` for anything unparseable, which every consumer
    already treats as "unknown age" rather than "fresh".
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()



def _safe_detail(status: int, body: Any) -> str:
    """Upstream detail for an error message — except on an auth failure.

    These RuntimeErrors reach the application log through the ingest's
    own handler, and an upstream body is genuinely useful for diagnosing
    one: a 422 names the parameter it disliked.  A 401/403 body is the
    one shape that can echo the credential back, and some APIs do.  The
    status code alone already says everything an operator needs there —
    the key is wrong — so nothing is lost by refusing to repeat it.
    """
    if status in (401, 403):
        return "authentication rejected (response body withheld)"
    return str(body)[:200]


class OrientEldRateGate:
    """Per-group rolling-window gate, shared by every call on one key.

    The window is per CLIENT because the vendor's budget is per API key
    — two companies' keys never contend with each other, and pretending
    otherwise would halve a multi-company account's throughput for no
    upstream reason.
    """

    def __init__(self) -> None:
        # ONE LOCK PER GROUP, and that is not a micro-optimisation.
        # A single lock is held across the ``sleep`` below, so a
        # directory read (90/minute) queued behind a tracking read's
        # six-second wait would serve its six seconds too — measured,
        # not theorised: both acquired at +6.00s.  The groups have
        # independent budgets upstream and must have independent locks
        # here.  Built eagerly from ``_GROUPS`` because creating one
        # lazily under a race creates two, and two locks guard nothing.
        self._locks = {g: asyncio.Lock() for g in _GROUPS}
        self._windows: dict[str, list[float]] = {}

    async def acquire(self, group: str) -> None:
        limit, window = _GROUPS.get(group, _GROUPS["tracking"])
        lock = self._locks.get(group) or self._locks["tracking"]
        async with lock:
            now = time.monotonic()
            stamps = [t for t in self._windows.get(group, [])
                      if t > now - window]
            if len(stamps) >= limit:
                wait = stamps[0] + window - now
                if wait > 0:
                    logger.debug(
                        "orient_eld throttling group=%s sleeping %.2fs "
                        "(%d/%d in window)",
                        group, wait, len(stamps), limit,
                    )
                    await asyncio.sleep(wait)
                now = time.monotonic()
                stamps = [t for t in stamps if t > now - window]
            stamps.append(now)
            self._windows[group] = stamps


class OrientEldClient:
    """One ORIENT ELD API key — that is, one company.

    Construction is cheap and does no I/O; the aiohttp session is built
    on first use so an instance can be created inside a synchronous
    factory without a running loop.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = ORIENT_BASE_URL,
        company_code: str = "",
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("orient_eld requires a non-empty api_key")
        self._key = key
        self._base = base_url.rstrip("/")
        self._company_code = company_code
        self._session: Optional[aiohttp.ClientSession] = None
        self._gate = OrientEldRateGate()

    @property
    def company_code(self) -> str:
        return self._company_code

    @property
    def base_url(self) -> str:
        return self._base

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "x-api-key": self._key,
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Single rate-gated GET.  Returns ``(status, body)``.

        Non-2xx does not raise — callers build their own messages, and
        the connect probe in particular needs the upstream's own words
        rather than a stack trace.
        """
        await self._gate.acquire(_group_for(path))
        sess = await self._ensure_session()
        url = self._base + "/" + path.lstrip("/")
        async with sess.get(url, params=params) as resp:
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                body: Any = await resp.json()
            else:
                body = await resp.text()
            return resp.status, body

    async def _list_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = MAX_PAGES,
    ) -> list[dict]:
        """Every page of a ``{items, total, page, size}`` endpoint.

        Raises on the first non-2xx page rather than returning what it
        has: a partial roster silently missing its last page is the
        same failure class as answering zero, and the caller above
        turns an exception into "the feed did not run" while it would
        turn a short list into "these are all your drivers".
        """
        out: list[dict] = []
        page = 1
        while page <= max_pages:
            merged = dict(params or {})
            merged.update({"page": page, "size": MAX_PAGE_SIZE})
            status, body = await self._get(path, params=merged)
            if status >= 400:
                raise RuntimeError(
                    f"orient_eld {path} page {page} "
                    f"HTTP {status}: {_safe_detail(status, body)}",
                )
            if not isinstance(body, dict):
                raise RuntimeError(
                    f"orient_eld {path} page {page}: expected an object, "
                    f"got {type(body).__name__}",
                )
            items = body.get("items") or []
            out.extend(i for i in items if isinstance(i, dict))
            total = body.get("total")
            if not items or (isinstance(total, int) and len(out) >= total):
                return out
            page += 1
        logger.warning(
            "orient_eld %s stopped at the %d-page cap company=%s — "
            "%d rows collected; the rest of the list was NOT read",
            path, max_pages, self._company_code or "?", len(out),
        )
        return out

    # ── Reads ─────────────────────────────────────────────────────

    async def get_company_info(self) -> dict:
        """Name, DOT, MC and the company's own timezone.

        The cheapest authenticated call the vendor offers, which is why
        it is also the connect probe.
        """
        status, body = await self._get("/api/companies/info")
        if status >= 400 or not isinstance(body, dict):
            raise RuntimeError(
                f"orient_eld company info HTTP {status}: "
                f"{_safe_detail(status, body)}",
            )
        return body

    async def get_tracking(self) -> list[dict]:
        """Every driver currently reporting: status, since when, where.

        This is the hours-of-service feed, and the only one — see the
        module docstring for what it deliberately does not contain.
        """
        return await self._list_all("/api/logs/tracking")

    # The two reads below have NO CALLER yet, and that is stated rather
    # than hidden.  They are what the driver-link work needs — matching
    # ORIENT's ``driver_id`` to a member of our roster, which is why
    # every ORIENT row currently arrives unlinked with an empty truck
    # column.  Delete them if that work is abandoned; do not quietly
    # leave them looking used.

    async def get_drivers(self) -> list[dict]:
        """The company's driver roster, with licence and contact.

        Driver PII — licence number, phone, email.  For matching a
        provider driver id to one of ours, never for storing wholesale.
        """
        return await self._list_all("/api/drivers")

    async def get_vehicles(self) -> list[dict]:
        """The company's vehicles: make, model, VIN, plate, unit number."""
        return await self._list_all("/api/vehicles")

    async def test_connection(self) -> tuple[bool, str, dict | None]:
        """Probe with the cheapest authenticated read.

        Returns ``(ok, message, meta)``.  The message is what the
        dashboard renders verbatim, so it names the company on success
        — an operator pasting five keys needs to see WHICH company each
        one opened, not just that it worked.
        """
        try:
            status, body = await self._get("/api/companies/info")
        except asyncio.TimeoutError:
            return False, "timeout after 20s — ORIENT ELD did not respond", None
        except aiohttp.ClientError as e:
            return False, f"could not reach ORIENT ELD: {e}", None
        if status in (401, 403):
            return False, "API key rejected by ORIENT ELD", None
        if status == 429:
            return False, "rate limited by ORIENT ELD — try again shortly", None
        if status >= 400 or not isinstance(body, dict):
            return False, f"ORIENT ELD returned HTTP {status}", None
        name = str(body.get("name") or "").strip()
        dot = str(body.get("dot_number") or "").strip()
        label = name or "company"
        if dot:
            label = f"{label} (DOT {dot})"
        return True, f"connected to {label}", body


class MultiCompanyOrientClient:
    """Every company's key on one account, asked together.

    The account is the unit the platform schedules on; the API key is
    the unit ORIENT issues.  This closes that gap the same way the
    Samsara client does — fan out, tag each row with the company it
    came from, and let one company's outage cost only that company's
    rows.
    """

    def __init__(
        self,
        clients: dict[str, OrientEldClient],
        *,
        account_id: int | None = None,
    ) -> None:
        self._clients = dict(clients)
        self._account_id = account_id

    @property
    def company_codes(self) -> list[str]:
        return sorted(self._clients)

    def __len__(self) -> int:
        return len(self._clients)

    async def close(self) -> None:
        for c in self._clients.values():
            try:
                await c.close()
            except Exception:  # pragma: no cover - close is best-effort
                logger.exception("orient_eld: client close failed")

    async def _fan_out(self, method: str) -> list[dict]:
        """Call ``method`` on every company client, tagging each row.

        One company failing must not empty the account: its exception
        is logged and its rows are absent, while the others' rows still
        arrive.  The alternative — one raise for the whole account — is
        how a single expired key makes an entire fleet look off duty.
        """
        async def _one(code: str, client: OrientEldClient) -> list[dict]:
            rows = await getattr(client, method)()
            for r in rows:
                r["_company_code"] = code
            return rows

        results = await asyncio.gather(
            *(_one(code, c) for code, c in self._clients.items()),
            return_exceptions=True,
        )
        out: list[dict] = []
        for code, res in zip(self._clients, results):
            if isinstance(res, BaseException):
                if isinstance(res, asyncio.CancelledError):
                    raise res
                # ``logger.exception`` reads ``sys.exc_info()``, which is
                # empty here — this is a value returned by ``gather``, not
                # a live exception being handled, so it logged
                # "NoneType: None" where the traceback should be.  Passing
                # the exception as ``exc_info`` attaches its real one,
                # which is the whole point of logging a company's feed
                # failing at all.
                logger.error(
                    "orient_eld %s failed acct=%s company=%s: %s",
                    method, self._account_id, code, res, exc_info=res,
                )
                continue
            out.extend(res)
        return out

    async def get_tracking(self) -> list[dict]:
        return await self._fan_out("get_tracking")

    async def get_drivers(self) -> list[dict]:
        return await self._fan_out("get_drivers")

    async def get_vehicles(self) -> list[dict]:
        return await self._fan_out("get_vehicles")

    async def test_connection(self) -> tuple[bool, str, dict | None]:
        """Green only when EVERY configured key works.

        A partial success is reported as a failure with the failing
        companies named: an account that connects with three of five
        keys has three-fifths of its drivers invisible, and the honest
        moment to say so is now rather than when a dispatcher notices
        somebody missing.
        """
        if not self._clients:
            return False, "no ORIENT ELD API key configured", None
        results = await asyncio.gather(
            *(c.test_connection() for c in self._clients.values()),
            return_exceptions=True,
        )
        good: list[str] = []
        bad: list[str] = []
        first_meta: dict | None = None
        for code, res in zip(self._clients, results):
            if isinstance(res, BaseException):
                if isinstance(res, asyncio.CancelledError):
                    raise res
                bad.append(f"{code}: {res}")
                continue
            ok, message, meta = res
            if ok:
                good.append(f"{code} → {message}")
                if first_meta is None:
                    first_meta = meta
            else:
                bad.append(f"{code}: {message}")
        if bad:
            return False, "; ".join(bad), first_meta
        return True, "; ".join(good), first_meta


def build_multi_company_orient_client(
    creds: dict[str, Any],
    *,
    account_id: int | None = None,
    base_url: str = ORIENT_BASE_URL,
) -> MultiCompanyOrientClient:
    """Build the fan-out from an integration row's credentials blob.

    Accepts the same two shapes the Samsara card already stores, so an
    operator learns one mental model for "a key per company":

      ``{"companies": {"<company code>": "<key>"}}`` — the canonical
      form, one key per company.

      ``{"api_key": "<key>"}`` — the single-company shorthand, filed
      under the code ``"default"``.  ORIENT issues one key per company,
      so an account with exactly one company should not have to invent
      a code for it.

    A company whose key is blank is SKIPPED rather than built with an
    empty header: an empty key 401s on every call and spends the
    account's budget to learn nothing.

    Note the deliberate divergence from ``build_multi_company_client``
    next door.  Samsara's builder falls back to a single account-level
    token for any company that has no key of its own.  This one does
    NOT: an ORIENT key is scoped to one company, so lending company A's
    key to company B does not fail — it succeeds, and returns A's
    drivers under B's name.  A company with no key of its own is
    absent, which is visible; a company wearing someone else's drivers
    is not.  The account-level ``api_key`` is therefore only read when
    the per-company map is EMPTY, which is the single-company case and
    the state a fresh connect lands in.
    """
    creds = creds or {}
    raw = creds.get("companies")
    pairs: dict[str, str] = {}
    if isinstance(raw, dict) and raw:
        for code, key in raw.items():
            text = str(key or "").strip()
            if text:
                pairs[str(code)] = text
            else:
                logger.warning(
                    "orient_eld: company %s has no API key acct=%s — skipped",
                    code, account_id,
                )
    else:
        single = str(creds.get("api_key") or creds.get("api_token") or "").strip()
        if single:
            pairs["default"] = single
    clients = {
        code: OrientEldClient(key, base_url=base_url, company_code=code)
        for code, key in pairs.items()
    }
    return MultiCompanyOrientClient(clients, account_id=account_id)
