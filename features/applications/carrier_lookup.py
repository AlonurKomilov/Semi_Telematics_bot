"""FMCSA carrier lookup — the apply form's employer autocomplete.

Provider chain (isolated here so swapping is a one-file change):

  1. **QCMobile** (``FMCSA_WEBKEY`` env set) — FMCSA's real-time carrier API.
     Registration currently requires Login.gov access, so this is the
     future path: the moment a key lands in ``.env`` it takes over.
  2. **Company Census File** (default, keyless) — the same registry
     published openly on data.transportation.gov (Socrata).  An optional
     ``SODA_APP_TOKEN`` raises the anonymous rate limits; never required.

Both map into one shape: ``{name, dba, city, state, phone, dot_number,
active}``.  Strictly best-effort — every failure returns ``[]`` and the
form simply shows no suggestions.  A small TTL cache keeps repeat
keystrokes/queries off the upstream.
"""
from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger("applications.carrier_lookup")

_CENSUS_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"
_QCMOBILE_URL = "https://mobile.fmcsa.dot.gov/qc/services/carriers/name/{name}"

# query → (expires_at, results).  Autocomplete traffic is extremely
# repetitive (each keystroke of every applicant), so even a small cache
# absorbs most of it.  Size-capped by evicting the oldest entries.
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 6 * 3600
_CACHE_MAX = 500


def _clean_query(q: str) -> str:
    """Uppercase, strip anything that could escape a SoQL/URL string."""
    return re.sub(r"[^A-Z0-9 &.\-']", "", (q or "").upper().strip())[:60]


async def _census_search(q: str, limit: int) -> list[dict]:
    """The keyless open-data path (Socrata SoQL)."""
    import aiohttp

    # Apostrophes are legitimate in carrier names (O'BRIEN…) but delimit SoQL
    # string literals — double them, the SoQL escape.
    sq = q.replace("'", "''")
    params = {
        "$select": ("dot_number,legal_name,dba_name,phy_city,phy_state,phone,"
                    "status_code,email_address,docket1prefix,docket1"),
        "$where": f"starts_with(upper(legal_name), '{sq}') OR starts_with(upper(dba_name), '{sq}')",
        "$order": "status_code, legal_name",   # 'A'ctive sorts before 'I'nactive
        "$limit": str(limit),
    }
    headers = {}
    token = os.getenv("SODA_APP_TOKEN", "").strip()
    if token:
        headers["X-App-Token"] = token
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(_CENSUS_URL, params=params, headers=headers) as resp:
            if resp.status != 200:
                logger.debug("census lookup %s -> %s", q, resp.status)
                return []
            rows = await resp.json()
    return [{
        "name": r.get("legal_name") or "",
        "dba": r.get("dba_name") or "",
        "city": r.get("phy_city") or "",
        "state": r.get("phy_state") or "",
        "phone": r.get("phone") or "",
        "dot_number": r.get("dot_number") or "",
        "mc_number": (r.get("docket1") or "") if (r.get("docket1prefix") or "MC") == "MC" else "",
        # The registry's contact email — often the safety department.  It
        # prefills the recruiter's §391.23 request address (editable there).
        "email": (r.get("email_address") or "").lower(),
        "active": (r.get("status_code") or "") == "A",
    } for r in rows if isinstance(r, dict)]


async def _qcmobile_search(q: str, limit: int, webkey: str) -> list[dict]:
    """FMCSA's real-time API — used once a webKey is configured."""
    import aiohttp
    from urllib.parse import quote

    url = _QCMOBILE_URL.format(name=quote(q))
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"webKey": webkey, "size": str(limit)}) as resp:
            if resp.status != 200:
                logger.debug("qcmobile lookup %s -> %s", q, resp.status)
                return []
            data = await resp.json(content_type=None)
    items = data.get("content") or []
    out: list[dict] = []
    for it in items[:limit]:
        c = (it or {}).get("carrier") or it or {}
        out.append({
            "name": c.get("legalName") or "",
            "dba": c.get("dbaName") or "",
            "city": c.get("phyCity") or "",
            "state": c.get("phyState") or "",
            "phone": c.get("telephone") or c.get("phone") or "",
            "dot_number": str(c.get("dotNumber") or ""),
            "mc_number": str(c.get("docketNumber") or ""),
            "email": str(c.get("emailAddress") or "").lower(),
            "active": str(c.get("allowedToOperate") or c.get("statusCode") or "").upper() in ("Y", "A"),
        })
    return out


async def search_carriers(q: str, limit: int = 8) -> list[dict]:
    """Autocomplete entry point.  Empty list on ANY failure — never raises."""
    q = _clean_query(q)
    if len(q) < 3:
        return []
    now = time.monotonic()
    hit = _CACHE.get(q)
    if hit and hit[0] > now:
        return hit[1]
    results: list[dict] = []
    webkey = os.getenv("FMCSA_WEBKEY", "").strip()
    try:
        if webkey:
            results = await _qcmobile_search(q, limit, webkey)
        if not results:
            results = await _census_search(q, limit)
    except Exception as e:   # noqa: BLE001 — best-effort by contract
        logger.debug("carrier lookup failed for %r: %s", q, e)
        results = []
    if len(_CACHE) >= _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:50]:
            _CACHE.pop(k, None)
    _CACHE[q] = (now + _CACHE_TTL, results)
    return results
