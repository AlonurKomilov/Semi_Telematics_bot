"""CDL photo → structured fields (the apply form's "fast-fill").

One vision call reads the front of an applicant's licence and returns the
identity + licence details the form can prefill (Step 2 name/DOB/address,
Step 3 CDL number/state/class/expiration/endorsements).  Strictly
best-effort: any failure returns ``None`` and the applicant types manually.

The model's output is NEVER trusted raw — ``_sanitize_fields`` whitelists
keys, validates enums/dates/formats, and caps lengths, so a hallucinated or
adversarial response can't inject junk into the form.  The image is
processed in memory only; nothing is stored at OCR time (the same photo is
uploaded again as the CDL-Front document at submit).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger("applications.ocr")

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}
# The form's endorsement chips.  'X' (combined tank+hazmat) is folded into
# H + N to match the form's de-duplicated list.
_ENDORSEMENTS = {"H", "N", "T", "P", "S"}

_PROMPT = """Read this photo of the FRONT of a US commercial driver's license (CDL).
Return ONLY a JSON object — no prose, no markdown fences — with these keys:

  first, middle, last        licence holder's name parts (strings)
  dob                        date of birth, YYYY-MM-DD
  addr1, city, state, zip    split the address printed on the licence into
                             street line, city, two-letter state, and the
                             ZIP code (the 5 or 9 digits at the END of the
                             address — always include it when readable)
  number                     the licence/DL number
  issue_state                two-letter state that issued the licence
  cdl_class                  "A", "B" or "C"
  exp                        expiration date, YYYY-MM-DD
  endorsements               array of endorsement letter codes (e.g. ["H","N"])
  restrictions               restrictions text, or ""

Use null for anything you cannot read clearly — NEVER guess.
If this is not a driver's license, return {"not_license": true}."""

_SYSTEM = (
    "You are a precise document-OCR engine. You output only valid JSON and "
    "never invent values you cannot clearly read."
)


def _clean_str(v: object, cap: int) -> str:
    """Trim, strip control characters, cap length.  '' when unusable."""
    if not isinstance(v, str):
        return ""
    s = re.sub(r"[\x00-\x1f\x7f]", "", v).strip()
    return s[:cap]


def _clean_date(v: object) -> str:
    """Normalise to YYYY-MM-DD; '' when unparseable (never guess)."""
    if not isinstance(v, str) or not v.strip():
        return ""
    s = v.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _parse_response(text: str) -> dict | None:
    """Model text → dict.  Tolerates ```json fences; None on anything else."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        out = json.loads(s)
    except (ValueError, TypeError):
        return None
    return out if isinstance(out, dict) else None


def _sanitize_fields(raw: dict) -> dict | None:
    """Whitelist + validate the model's JSON into safe prefill values.

    Returns only keys that carry a usable value; ``None`` when nothing
    survived (or the model said it isn't a licence).
    """
    if not isinstance(raw, dict) or raw.get("not_license"):
        return None
    out: dict[str, object] = {}

    for k, cap in (("first", 50), ("middle", 50), ("last", 50),
                   ("addr1", 100), ("city", 50), ("number", 25),
                   ("restrictions", 100)):
        v = _clean_str(raw.get(k), cap)
        if v:
            out[k] = v

    for k in ("dob", "exp"):
        v = _clean_date(raw.get(k))
        if v:
            out[k] = v

    for k in ("state", "issue_state"):
        v = _clean_str(raw.get(k), 2).upper()
        if v in _US_STATES:
            out[k] = v

    # ZIP: normalise rather than reject — models return "29609", "29609-5442"
    # or "296095442"; all are the same code.
    zip_digits = re.sub(r"\D", "", _clean_str(raw.get("zip"), 12))
    if len(zip_digits) == 9:
        out["zip"] = f"{zip_digits[:5]}-{zip_digits[5:]}"
    elif len(zip_digits) == 5:
        out["zip"] = zip_digits

    cls = _clean_str(raw.get("cdl_class"), 1).upper()
    if cls in ("A", "B", "C"):
        out["cdl_class"] = cls

    codes: set[str] = set()
    if isinstance(raw.get("endorsements"), list):
        for c in raw["endorsements"]:
            code = _clean_str(c, 1).upper()
            if code == "X":            # combined tank+hazmat → the two chips
                codes.update({"H", "N"})
            elif code in _ENDORSEMENTS:
                codes.add(code)
    if codes:
        out["endorsements"] = sorted(codes)

    return out or None


async def extract_cdl_fields(image_bytes: bytes, account_id: int) -> dict | None:
    """Run the vision model over a CDL-front photo → sanitized prefill dict.

    Best-effort by contract: every failure path (model error, junk output,
    unreadable photo) returns ``None`` so the form falls back to manual
    entry.  Extracted values are never logged (they're PII).
    """
    if not image_bytes:
        return None
    try:
        # Local import — keeps this module light for unit tests of the
        # parser/sanitizer (the AI stack pulls heavy SDK deps).
        from capabilities.ai.vision import generate_with_vision

        text, _usage = await generate_with_vision(
            _PROMPT, image_bytes, system=_SYSTEM,
            account_id=account_id, action="cdl_ocr",
        )
        parsed = _parse_response(text)
        fields = _sanitize_fields(parsed) if parsed else None
        logger.info(
            "cdl_ocr account=%s extracted=%s",
            account_id, sorted(fields.keys()) if fields else "none",
        )
        return fields
    except Exception as e:                       # noqa: BLE001 — best-effort
        logger.warning("cdl_ocr failed account=%s: %s", account_id, e)
        return None
