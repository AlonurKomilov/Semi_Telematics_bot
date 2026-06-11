"""Vision / camera analysis using Gemini multimodal."""

from __future__ import annotations

import logging

from capabilities.ai.registry import DEFAULT_VISION_MODEL, DEFAULT_VISION_LOCATION
from capabilities.ai.models import _account_vision_models, _ensure_model
from capabilities.ai.generation import generate, _is_rate_limit_error, _capture_usage

logger = logging.getLogger("bot.ai")


def _sniff_image_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes.

    google-genai's ``Part.from_bytes`` requires an explicit ``mime_type``
    (unlike the legacy ``Image.from_bytes`` which auto-detected).
    Samsara dashcam frames are JPEG in practice; everything else is a
    safe-ish fallback to JPEG since Vertex will reject genuinely
    malformed bytes itself.
    """
    if not data or len(data) < 8:
        return "image/jpeg"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


CAMERA_CHECK_SYSTEM = (
    "You are a fleet dashcam quality inspector. Analyze the provided dashcam "
    "frame and evaluate:\n"
    "1. OBSTRUCTION: Is the camera view blocked or partially obstructed "
    "(e.g. sticker, dirt, object, hand, sun visor, phone mount)?\n"
    "2. ALIGNMENT: Is the camera centered on the road ahead? Or is it "
    "tilted too far up (showing mostly sky), too far down (showing mostly "
    "hood/dashboard), or angled left/right?\n"
    "3. IMAGE QUALITY: Is the image too dark, too bright/washed out, or blurry?\n\n"
    "Respond in EXACTLY this format (no extra text):\n"
    "STATUS: OK | WARNING | PROBLEM\n"
    "OBSTRUCTION: none | partial | full — brief description\n"
    "ALIGNMENT: centered | too_high | too_low | tilted_left | tilted_right — brief note\n"
    "QUALITY: good | dark | bright | blurry — brief note\n"
    "SUMMARY: One-sentence plain-language summary for the fleet team."
)


async def analyze_camera_image(
    image_bytes: bytes,
    vehicle_name: str = "",
    account_id: int | None = None,
    *,
    user_id: int | None = None,
    role: str | None = None,
    action: str = "vision",
) -> dict:
    """Analyze a dashcam image for obstruction and alignment issues.

    ``user_id`` / ``role`` / ``action`` enable per-attempt router
    telemetry — each vision model call inside the fallback loop
    writes an ai_usage row (success or failure) so the scorer keys
    on the same (account x role x model) dimensions chat does.
    Default action="vision" so background jobs that don't pass an
    action label still land in a coherent slice.
    """
    import asyncio
    from capabilities.ai.usage import (
        record_call_attempt as _record_call,
        classify_error as _classify_err,
    )
    import time as _t

    if not image_bytes:
        return {
            "status": "ERROR",
            "obstruction": "unknown",
            "alignment": "unknown",
            "quality": "unknown",
            "summary": "No image data available",
            "raw": "",
        }

    model_name = DEFAULT_VISION_MODEL
    location = DEFAULT_VISION_LOCATION

    if account_id is not None and account_id in _account_vision_models:
        model_name, location, _ = _account_vision_models[account_id]

    _VISION_FALLBACK = [
        ("gemini-2.5-flash", "us-central1"),
        ("gemini-2.5-pro", "us-central1"),
        ("gemini-3.1-flash-lite-preview", "global"),
        ("gemini-3.1-pro-preview", "global"),
    ]

    attempts = [(model_name, location)]
    for fb_name, fb_loc in _VISION_FALLBACK:
        if fb_name != model_name:
            attempts.append((fb_name, fb_loc))

    from google.genai import types as _gtypes

    image_part = _gtypes.Part.from_bytes(
        data=image_bytes,
        mime_type=_sniff_image_mime(image_bytes),
    )
    prompt_part = _gtypes.Part.from_text(
        text=CAMERA_CHECK_SYSTEM
        + (f"\n\nVehicle: {vehicle_name}" if vehicle_name else "")
    )

    # Per-call + total wall-clock budgets.  Without these the
    # ``generate_content`` thread can hang for many minutes when
    # Vertex AI is slow/unavailable, and with sem=3 in the
    # ``camera_check`` scheduler a few stuck images blow the 600s
    # job budget and the whole cycle times out with no result.
    # The current budgets keep a 50-truck fleet comfortably under
    # the camera job's 10-minute deadline (50 ÷ 3 concurrency × 25s
    # worst-case ≈ 7 minutes) while still letting a healthy call
    # finish in ~10 seconds.
    _VISION_PER_CALL_TIMEOUT_S = 25.0
    _VISION_TOTAL_BUDGET_S = 45.0
    _t0 = asyncio.get_event_loop().time()

    last_exc: Exception | None = None
    text = ""
    usage: dict | None = None
    for attempt_model, attempt_loc in attempts:
        # Stop trying fallbacks once we've burned the wall-clock
        # budget — keeps a single bad image from monopolising its
        # semaphore slot through 4-5 sequential 25s timeouts.
        _elapsed = asyncio.get_event_loop().time() - _t0
        _remaining = _VISION_TOTAL_BUDGET_S - _elapsed
        if _remaining <= 0:
            logger.warning(
                "Vision: total budget (%.0fs) exhausted before %s; "
                "giving up on this image",
                _VISION_TOTAL_BUDGET_S, attempt_model,
            )
            break
        _call_timeout = min(_VISION_PER_CALL_TIMEOUT_S, _remaining)
        _attempt_started = _t.monotonic()
        try:
            model_obj = _ensure_model(attempt_model, attempt_loc)
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model_obj.generate_content, [prompt_part, image_part],
                ),
                timeout=_call_timeout,
            )
            text = response.text.strip() if response.text else ""
            usage = _capture_usage(response)
            _latency_ms = int((_t.monotonic() - _attempt_started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=role, action=action,
                model=attempt_model, latency_ms=_latency_ms,
                error_type="ok", usage=usage,
                prompt_category=None,  # image-only — no text prompt to classify
            )
            if attempt_model != model_name:
                logger.info(
                    f"Vision fallback succeeded with {attempt_model} "
                    f"after {model_name} failed"
                )
            break
        except asyncio.TimeoutError as te:
            # Timeout = infrastructure slowness, not a model-specific
            # error.  Skipping straight to fallbacks here would just
            # eat more time on the same hung backend; bail out.
            last_exc = te
            _latency_ms = int((_t.monotonic() - _attempt_started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=role, action=action,
                model=attempt_model, latency_ms=_latency_ms,
                error_type="timeout", usage=None,
                prompt_category=None,
            )
            logger.warning(
                "Vision %s timed out after %.0fs — giving up on this image",
                attempt_model, _call_timeout,
            )
            break
        except Exception as e:
            last_exc = e
            _latency_ms = int((_t.monotonic() - _attempt_started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=role, action=action,
                model=attempt_model, latency_ms=_latency_ms,
                error_type=_classify_err(e), usage=None,
                prompt_category=None,
            )
            if _is_rate_limit_error(e):
                logger.warning(
                    f"Vision {attempt_model} rate-limited, trying next"
                )
                continue
            else:
                logger.error(f"Camera vision analysis failed ({attempt_model}): {e}")
                break
    else:
        logger.error(f"All vision models rate-limited: {last_exc}")
        return {
            "status": "ERROR",
            "obstruction": "unknown",
            "alignment": "unknown",
            "quality": "unknown",
            "summary": f"Analysis failed (rate limited): {last_exc}",
            "raw": "",
            "usage": None,
        }

    if last_exc and not text:
        return {
            "status": "ERROR",
            "obstruction": "unknown",
            "alignment": "unknown",
            "quality": "unknown",
            "summary": f"Analysis failed: {last_exc}",
            "raw": "",
            "usage": usage,
        }

    result = {
        "status": "OK",
        "obstruction": "none",
        "alignment": "centered",
        "quality": "good",
        "summary": text,
        "raw": text,
        "usage": usage,
    }
    def _keyword(raw: str) -> str:
        """Extract just the keyword before the em-dash description separator."""
        # AI returns e.g. "partial — brief note" or "centered — brief note"
        for sep in (" \u2014 ", " - ", "\u2014"):
            if sep in raw:
                return raw.split(sep, 1)[0].strip().lower()
        return raw.strip().lower()

    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("STATUS:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("OK", "WARNING", "PROBLEM"):
                result["status"] = val
        elif line.upper().startswith("OBSTRUCTION:"):
            result["obstruction"] = _keyword(line.split(":", 1)[1])
        elif line.upper().startswith("ALIGNMENT:"):
            result["alignment"] = _keyword(line.split(":", 1)[1])
        elif line.upper().startswith("QUALITY:"):
            result["quality"] = _keyword(line.split(":", 1)[1])
        elif line.upper().startswith("SUMMARY:"):
            result["summary"] = line.split(":", 1)[1].strip()

    return result


async def generate_with_vision(
    prompt: str,
    image_bytes: bytes,
    system: str = "You are a helpful assistant.",
    account_id: int | None = None,
    *,
    user_id: int | None = None,
    role: str | None = None,
    action: str = "vision_text",
) -> tuple[str, dict | None]:
    """Generate a text response from a prompt + image using Gemini vision.

    Returns ``(text, usage)`` — usage may be ``None`` if the backend
    didn't report token counts or if all vision attempts failed and we
    fell back to text-only ``generate()``.

    ``user_id`` / ``role`` / ``action`` enable per-attempt router
    telemetry parallel to the image-only ``analyze_camera_image``
    path; default action="vision_text" keeps text+image rows in a
    distinct scoring slice from raw camera-check rows.  Falls back
    to ``generate()`` when ``image_bytes`` is empty — that path
    already self-records, so no double-log.
    """
    import asyncio
    from capabilities.ai.usage import (
        record_call_attempt as _record_call,
        classify_error as _classify_err,
        classify_prompt as _classify_prompt,
    )
    import time as _t

    if not image_bytes:
        return await generate(prompt, system=system, account_id=account_id)

    model_name = DEFAULT_VISION_MODEL
    location = DEFAULT_VISION_LOCATION
    if account_id is not None and account_id in _account_vision_models:
        model_name, location, _ = _account_vision_models[account_id]

    _VISION_FALLBACK = [
        ("gemini-2.5-flash", "us-central1"),
        ("gemini-2.5-pro", "us-central1"),
    ]
    attempts = [(model_name, location)]
    for fb_name, fb_loc in _VISION_FALLBACK:
        if fb_name != model_name:
            attempts.append((fb_name, fb_loc))

    from google.genai import types as _gtypes

    image_part = _gtypes.Part.from_bytes(
        data=image_bytes,
        mime_type=_sniff_image_mime(image_bytes),
    )
    text_content = f"{system}\n\n{prompt}" if system else prompt
    prompt_part = _gtypes.Part.from_text(text=text_content)

    # Image-with-text path carries a real prompt; classify it once so
    # scoring slices match the chat path's prompt_category dimension.
    _prompt_category = _classify_prompt(prompt)

    last_exc: Exception | None = None

    for attempt_model, attempt_loc in attempts:
        _attempt_started = _t.monotonic()
        try:
            model_obj = _ensure_model(attempt_model, attempt_loc)
            response = await asyncio.to_thread(
                model_obj.generate_content, [prompt_part, image_part],
            )
            text = response.text.strip() if response.text else ""
            usage = _capture_usage(response)
            _latency_ms = int((_t.monotonic() - _attempt_started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=role, action=action,
                model=attempt_model, latency_ms=_latency_ms,
                error_type="ok", usage=usage,
                prompt_category=_prompt_category,
            )
            if attempt_model != model_name:
                logger.info(
                    "Vision fallback to %s after %s failed",
                    attempt_model, model_name,
                )
            if text:
                return text, usage
        except Exception as e:
            last_exc = e
            _latency_ms = int((_t.monotonic() - _attempt_started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=role, action=action,
                model=attempt_model, latency_ms=_latency_ms,
                error_type=_classify_err(e), usage=None,
                prompt_category=_prompt_category,
            )
            if _is_rate_limit_error(e):
                logger.warning("Vision %s rate-limited, trying next", attempt_model)
                continue
            logger.error("generate_with_vision failed (%s): %s", attempt_model, e)
            break

    logger.info("Vision unavailable, falling back to text-only generate")
    return await generate(prompt, system=system, account_id=account_id)
