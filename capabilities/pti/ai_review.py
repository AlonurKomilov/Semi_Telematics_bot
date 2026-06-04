"""Per-photo AI vision review for PTI inspection media.

When a driver captures a checklist photo (e.g. for "Mudflap"), the
Mini App fires a single-photo AI check against the component that
photo is supposed to show.  The model returns a structured verdict
that:

  * gives the driver instant feedback in the wizard ("looks OK" vs
    "possible damage — double-check"), and
  * rolls up into the final PTI report so the fleet reviewer sees the
    AI's read of each photo next to the driver's self-reported status.

This module owns the PTI-specific prompt + response parsing.  The
actual Vertex/Gemini multimodal call is delegated to
``capabilities.ai.vision.generate_with_vision`` so model selection,
per-account model preference, and the rate-limit fallback chain all
stay in one place.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("bot.pti.ai")


# Verdict vocabulary — kept small + stable so the dashboard/wizard can
# switch on it without parsing free text.
VALID_VERDICTS = {"ok", "possible_issue", "likely_defect", "unclear"}

# Coarse status stored on the media row alongside the JSON result.
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


_SYSTEM = (
    "You are a commercial-vehicle (DOT) pre-trip inspection assistant. "
    "A driver photographed ONE component of a truck or trailer for an "
    "inspection. Examine the photo for visible defects, damage, wear, "
    "leaks, or safety issues with THAT component only. Be conservative: "
    "if the component looks serviceable, say so; if you genuinely can't "
    "tell from the photo, say 'unclear' rather than guessing.\n\n"
    "Respond in EXACTLY this format, no extra text:\n"
    "VERDICT: ok | possible_issue | likely_defect | unclear\n"
    "CONFIDENCE: low | medium | high\n"
    "SUMMARY: one short plain-language sentence for the driver and fleet."
)


def _build_prompt(item_label: str, category: str, reported_status: str) -> str:
    """Compose the per-photo instruction with the component context.

    The driver's own reported status is included so the model can
    weigh in on agreement/disagreement — e.g. the driver marked the
    item OK but the photo shows a crack.
    """
    bits = [f"Component being inspected: {item_label or 'unknown component'}."]
    if category:
        bits.append(f"Category: {category}.")
    if reported_status and reported_status not in ("pending", ""):
        bits.append(
            f"The driver marked this item as '{reported_status}'. "
            "Say whether the photo appears consistent with that."
        )
    bits.append(
        "Look only at this component. Ignore background, other parts of "
        "the vehicle, and image framing — judge the component's condition."
    )
    return " ".join(bits)


def _parse_response(text: str) -> dict:
    """Parse the VERDICT/CONFIDENCE/SUMMARY block into a dict.

    Defensive: a model that ignores the format still yields a usable
    'unclear' verdict with the raw text as the summary rather than
    raising.
    """
    verdict = "unclear"
    confidence = "low"
    summary = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper.startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip().lower().replace(" ", "_")
            # Tolerate the model adding a trailing note after the keyword.
            v = v.split("—")[0].split("-")[0].strip("_ ").strip()
            for cand in VALID_VERDICTS:
                if v.startswith(cand):
                    verdict = cand
                    break
        elif upper.startswith("CONFIDENCE:"):
            c = line.split(":", 1)[1].strip().lower()
            if c.startswith("high"):
                confidence = "high"
            elif c.startswith("med"):
                confidence = "medium"
            else:
                confidence = "low"
        elif upper.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()

    if not summary:
        # Model didn't follow the format — fall back to the first
        # non-empty line so the reviewer still sees something useful.
        summary = next(
            (ln.strip() for ln in (text or "").splitlines() if ln.strip()),
            "No assessment returned.",
        )[:300]

    return {"verdict": verdict, "confidence": confidence, "summary": summary}


async def review_photo(
    image_bytes: bytes,
    *,
    item_label: str,
    category: str = "",
    reported_status: str = "",
    account_id: int | None = None,
) -> dict:
    """Run a single-photo AI vision review.

    Returns a verdict dict::

        {
          "verdict":    "ok|possible_issue|likely_defect|unclear",
          "confidence": "low|medium|high",
          "summary":    "<one line>",
          "model":      "<model name or 'vision'>",
        }

    Never raises for model/transport errors — the caller stores
    ``status='error'`` and the UI degrades to "AI check unavailable".
    Raises only on a programming error (e.g. empty image), which the
    route maps to a 4xx.
    """
    if not image_bytes:
        raise ValueError("image_bytes is required for AI review")

    # Lazy import keeps the PTI capability importable even when the AI
    # stack / vertex deps aren't installed in a given deployment.
    from capabilities.ai.vision import generate_with_vision

    prompt = _build_prompt(item_label, category, reported_status)
    text = await generate_with_vision(
        prompt,
        image_bytes,
        system=_SYSTEM,
        account_id=account_id,
    )
    result = _parse_response(text)

    # Stamp which model answered (best-effort — vision module tracks the
    # active model name on the account selection or default).
    try:
        from capabilities.ai.models import get_current_model_name
        result["model"] = get_current_model_name()
    except Exception:
        result["model"] = "vision"

    return result


def serialize(result: dict) -> str:
    """JSON-encode a verdict dict for the media row's result column."""
    return json.dumps(result, ensure_ascii=False)
