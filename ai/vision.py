"""Vision / camera analysis using Gemini multimodal."""

from __future__ import annotations

import logging

from ai.registry import DEFAULT_VISION_MODEL, DEFAULT_VISION_LOCATION
from ai.models import _account_vision_models, _ensure_model
# For _last_usage (reassigned via global), import the module.
from ai import generation as _gen_mod
from ai.generation import generate, _is_rate_limit_error

logger = logging.getLogger("bot.ai")

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
    "SUMMARY: One-sentence plain-language summary for the fleet manager."
)


async def analyze_camera_image(
    image_bytes: bytes,
    vehicle_name: str = "",
    account_id: int | None = None,
) -> dict:
    """Analyze a dashcam image for obstruction and alignment issues."""
    import asyncio

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

    from vertexai.generative_models import Part, Image

    image_part = Part.from_image(Image.from_bytes(image_bytes))
    prompt_part = Part.from_text(
        CAMERA_CHECK_SYSTEM
        + (f"\n\nVehicle: {vehicle_name}" if vehicle_name else "")
    )

    last_exc: Exception | None = None
    text = ""
    for attempt_model, attempt_loc in attempts:
        try:
            model_obj = _ensure_model(attempt_model, attempt_loc)
            response = await asyncio.to_thread(
                model_obj.generate_content, [prompt_part, image_part]
            )
            text = response.text.strip() if response.text else ""
            try:
                meta = response.usage_metadata
                prompt = getattr(meta, "prompt_token_count", 0) or 0
                reply = getattr(meta, "candidates_token_count", 0) or 0
                thinking = getattr(meta, "thoughts_token_count", 0) or 0
                total = getattr(meta, "total_token_count", 0) or 0
                if total < prompt + reply + thinking:
                    total = prompt + reply + thinking
                _gen_mod._last_usage = {
                    "prompt_tokens": prompt,
                    "reply_tokens": reply,
                    "thinking_tokens": thinking,
                    "total_tokens": total,
                }
            except Exception:
                _gen_mod._last_usage = None
            if attempt_model != model_name:
                logger.info(
                    f"Vision fallback succeeded with {attempt_model} "
                    f"after {model_name} failed"
                )
            break
        except Exception as e:
            last_exc = e
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
        }

    if last_exc and not text:
        return {
            "status": "ERROR",
            "obstruction": "unknown",
            "alignment": "unknown",
            "quality": "unknown",
            "summary": f"Analysis failed: {last_exc}",
            "raw": "",
        }

    result = {
        "status": "OK",
        "obstruction": "none",
        "alignment": "centered",
        "quality": "good",
        "summary": text,
        "raw": text,
    }
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("STATUS:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("OK", "WARNING", "PROBLEM"):
                result["status"] = val
        elif line.upper().startswith("OBSTRUCTION:"):
            result["obstruction"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("ALIGNMENT:"):
            result["alignment"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("QUALITY:"):
            result["quality"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("SUMMARY:"):
            result["summary"] = line.split(":", 1)[1].strip()

    return result


async def generate_with_vision(
    prompt: str,
    image_bytes: bytes,
    system: str = "You are a helpful assistant.",
    account_id: int | None = None,
) -> str:
    """Generate a text response from a prompt + image using Gemini vision."""
    import asyncio

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

    from vertexai.generative_models import Part, Image

    image_part = Part.from_image(Image.from_bytes(image_bytes))
    text_content = f"{system}\n\n{prompt}" if system else prompt
    prompt_part = Part.from_text(text_content)

    last_exc: Exception | None = None

    for attempt_model, attempt_loc in attempts:
        try:
            model_obj = _ensure_model(attempt_model, attempt_loc)
            response = await asyncio.to_thread(
                model_obj.generate_content, [prompt_part, image_part],
            )
            text = response.text.strip() if response.text else ""
            try:
                meta = response.usage_metadata
                prompt = getattr(meta, "prompt_token_count", 0) or 0
                reply = getattr(meta, "candidates_token_count", 0) or 0
                thinking = getattr(meta, "thoughts_token_count", 0) or 0
                total = getattr(meta, "total_token_count", 0) or 0
                if total < prompt + reply + thinking:
                    total = prompt + reply + thinking
                _gen_mod._last_usage = {
                    "prompt_tokens": prompt,
                    "reply_tokens": reply,
                    "thinking_tokens": thinking,
                    "total_tokens": total,
                }
            except Exception:
                _gen_mod._last_usage = None
            if attempt_model != model_name:
                logger.info(
                    "Vision fallback to %s after %s failed",
                    attempt_model, model_name,
                )
            if text:
                return text
        except Exception as e:
            last_exc = e
            if _is_rate_limit_error(e):
                logger.warning("Vision %s rate-limited, trying next", attempt_model)
                continue
            logger.error("generate_with_vision failed (%s): %s", attempt_model, e)
            break

    logger.info("Vision unavailable, falling back to text-only generate")
    return await generate(prompt, system=system, account_id=account_id)
