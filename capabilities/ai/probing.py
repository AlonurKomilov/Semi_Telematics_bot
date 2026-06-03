"""Live model availability probing against Vertex AI."""

from __future__ import annotations

import logging
import time

from capabilities.ai.registry import (
    MODEL_REGISTRY,
    _get_credentials,
    _maas_base_url,
    _anthropic_url,
    _mistral_url,
)

logger = logging.getLogger("bot.ai")

_availability_cache: dict[str, list[str]] = {}
_availability_ts: float = 0.0
_AVAILABILITY_TTL = 3600

_probe_scheduled = False


# Markers that distinguish a *permanent quota=0* 429 from a *transient
# throttle* 429.  When the project has 0 quota for a base model (e.g.
# Anthropic models not yet enabled / quota-increase request not yet
# approved), Vertex returns 429 with this exact wording — the model
# would never work for any user until the quota is provisioned.  We
# treat those as UNAVAILABLE so the model picker doesn't show models
# that everyone who taps them gets a 429 from.
#
# Transient 429s (true rate limits under load) come without these
# markers; we treat those as available so we don't flap a real model
# out of the picker just because a smoke test got throttled.
_PERMANENT_QUOTA_MARKERS = (
    "per_base_model",                           # global_online_prediction_requests_per_base_model
    "submit a quota increase",                  # standard quota=0 nudge
    "RESOURCE_EXHAUSTED",                       # explicit gRPC status
    "Quota exceeded",                           # generic precursor (paired with one of the above)
)


def _looks_like_permanent_quota_zero(body_text: str) -> bool:
    """True when a 429 body looks like a permanent quota=0, not a transient throttle."""
    if not body_text:
        return False
    return any(m in body_text for m in _PERMANENT_QUOTA_MARKERS)


def _probe_single(model: str, region: str, token: str, project: str) -> bool:
    """Send a tiny generateContent request; return True if reachable."""
    import requests
    if region == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{region}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/"
        f"projects/{project}/locations/{region}/"
        f"publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
        "generationConfig": {"maxOutputTokens": 5},
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 429 and not _looks_like_permanent_quota_zero(r.text):
            return True
        return False
    except Exception:
        return False


def _probe_openai_compat(model_id: str, region: str, token: str,
                         project: str) -> bool:
    """Send a tiny chat/completions request to the MaaS endpoint."""
    import requests
    url = _maas_base_url(region, project)
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "OK"}],
        "max_tokens": 5,
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=15,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 429 and not _looks_like_permanent_quota_zero(r.text):
            return True
        return False
    except Exception:
        return False


def _probe_anthropic(model_id: str, region: str, token: str,
                     project: str) -> bool:
    """Send a tiny Anthropic rawPredict request; return True if reachable."""
    import requests
    url = _anthropic_url(region, project, model_id)
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "messages": [{"role": "user", "content": "OK"}],
        "max_tokens": 5,
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=15,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 429 and not _looks_like_permanent_quota_zero(r.text):
            return True
        return False
    except Exception:
        return False


def _probe_mistral(model_id: str, publisher: str, region: str,
                   token: str, project: str) -> bool:
    """Send a tiny Mistral rawPredict request; return True if reachable."""
    import requests
    url = _mistral_url(region, project, publisher, model_id)
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "OK"}],
        "max_tokens": 5,
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=15,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 429 and not _looks_like_permanent_quota_zero(r.text):
            return True
        return False
    except Exception:
        return False


def probe_model_availability(force: bool = False) -> dict[str, list[str]]:
    """Probe every model×region and cache which ones actually work.

    Returns ``{model_name: [working_regions, …], …}``.
    """
    global _availability_cache, _availability_ts
    import os
    from google.auth.transport.requests import Request

    if not force and _availability_cache and (time.time() - _availability_ts < _AVAILABILITY_TTL):
        return _availability_cache

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        return {name: info["locations"] for name, info in MODEL_REGISTRY.items()}

    if not force and not _availability_cache:
        _schedule_background_probe()
        return {name: info["locations"] for name, info in MODEL_REGISTRY.items()}

    creds.refresh(Request())
    token = creds.token

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _do_probe(model_name, info, region):
        api_type = info.get("api_type", "gemini")
        if api_type == "openai_compat":
            ok = _probe_openai_compat(
                info["maas_model_id"], region, token, project,
            )
        elif api_type == "anthropic":
            ok = _probe_anthropic(
                info["anthropic_model_id"], region, token, project,
            )
        elif api_type == "mistral_raw":
            ok = _probe_mistral(
                info["mistral_model_id"],
                info.get("mistral_publisher", "mistralai"),
                region, token, project,
            )
        else:
            ok = _probe_single(model_name, region, token, project)
        return (model_name, region, ok)

    result: dict[str, list[str]] = {}
    futures = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for model_name, info in MODEL_REGISTRY.items():
            for region in info["locations"]:
                futures.append(pool.submit(_do_probe, model_name, info, region))
        for fut in as_completed(futures):
            mname, region, ok = fut.result()
            if ok:
                result.setdefault(mname, []).append(region)
    for mname in result:
        info = MODEL_REGISTRY.get(mname, {})
        total = len(info.get("locations", []))
        logger.info(
            f"Probe {mname}: {len(result[mname])}/{total} regions OK"
        )

    # Poisoning guard: if every probe failed (Vertex AI outage, DNS
    # failure, expired credentials, network partition…) we'd end up
    # with an empty ``result`` dict.  That would then be cached for an
    # hour, and ``_probe_filtered_registry()`` would render the model
    # picker empty for every user for the whole TTL — turning a
    # transient outage into a 1-hour bot blackout.
    #
    # Treat a zero-model probe as a failure: keep whatever the cache
    # had before, log the outage, and let the next scheduled probe
    # try again.  If this is the very first probe and the cache is
    # also empty, return the empty result anyway (caller will fall
    # back to the unfiltered registry as a permissive default).
    if not result and _availability_cache:
        logger.warning(
            "Probe returned 0 working models — Vertex AI likely down. "
            "Keeping previous availability cache (%d models) instead of "
            "blanking the picker.", len(_availability_cache),
        )
        return _availability_cache

    _availability_cache = result
    _availability_ts = time.time()
    logger.info(f"Availability probe complete: {len(result)} models available")
    return result


def _schedule_background_probe():
    """Run the full probe in a background thread (non-blocking)."""
    global _probe_scheduled
    if _probe_scheduled:
        return
    _probe_scheduled = True

    import threading

    def _run():
        global _probe_scheduled
        try:
            probe_model_availability(force=True)
        except Exception as e:
            logger.warning(f"Background probe failed: {e}")
        finally:
            _probe_scheduled = False

    threading.Thread(target=_run, daemon=True).start()
