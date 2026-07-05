"""AI theme maker for the apply form — palette candidates from the logo.

One vision call reads the carrier's logo (plus the recruiter's optional
style wishes) and proposes THREE full theme palettes for the apply form's
five colour slots.  The model only *proposes*: ``_sanitize_palettes``
enforces the rules — valid hex everywhere, no mid-tone surfaces (the zone
where neither black nor white text is crisp), readable heading-on-header —
nudging or dropping anything that fails.  Mirrors the frontend's sRGB
luminance math (public/theme.ts) so both layers agree on "readable".
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("applications.theme_ai")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLOTS = ("surface", "accent", "header", "bg", "heading")

_SYSTEM = (
    "You are a brand designer with deep colour-theory expertise. "
    "You output only valid JSON — no prose, no markdown fences."
)

_PROMPT = """Design THREE theme palettes for a trucking carrier's public job-application page{logo_note}.

Style wishes from the recruiter: {wishes}

Each palette fills five slots:
  surface — the page/card base colour (very light OR clearly dark; NEVER a
            mid-tone, because neither black nor white text reads on one)
  accent  — buttons/links/progress; should carry the brand's energy and be
            readable as white-text-on-accent
  header  — the hero/header band behind the title
  bg      — the page background behind the card (subtle, harmonises with surface)
  heading — the title text colour ON the header band (must contrast with header)

Apply colour theory: build from the logo's actual colours when given,
use complementary or analogous relationships, keep sufficient contrast.
Make the three palettes genuinely different moods.

Return ONLY JSON:
{{"palettes": [{{"name": "<2-4 word mood name>", "surface": "#rrggbb",
"accent": "#rrggbb", "header": "#rrggbb", "bg": "#rrggbb",
"heading": "#rrggbb"}}, ...exactly 3...]}}"""


def _lum(hex_color: str) -> float:
    """sRGB luminance 0..1 — same formula as public/theme.ts."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _mix(hex_color: str, other: str, pct: float) -> str:
    """Blend ``pct`` of ``other`` into ``hex_color`` (both #rrggbb)."""
    a, b = hex_color.lstrip("#"), other.lstrip("#")
    out = "".join(
        f"{round(int(a[i:i + 2], 16) * (1 - pct) + int(b[i:i + 2], 16) * pct):02x}"
        for i in (0, 2, 4)
    )
    return f"#{out}"


def _fix_surface(hex_color: str) -> str:
    """Push a mid-tone surface (lum 0.42–0.62) out of the dead zone by
    blending toward white/black — whichever side it's already leaning."""
    lum = _lum(hex_color)
    if not 0.42 < lum < 0.62:
        return hex_color
    target = "#ffffff" if lum >= 0.52 else "#000000"
    fixed = hex_color
    for pct in (0.2, 0.4, 0.6, 0.8):
        fixed = _mix(hex_color, target, pct)
        lum2 = _lum(fixed)
        if not 0.42 < lum2 < 0.62:
            break
    return fixed


def _sanitize_palettes(raw: object) -> list[dict]:
    """Whitelist + enforce.  Anything that can't be repaired is dropped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("palettes"), list):
        return []
    out: list[dict] = []
    for p in raw["palettes"][:5]:
        if not isinstance(p, dict):
            continue
        colors = {k: str(p.get(k) or "").strip() for k in _SLOTS}
        if not all(_HEX_RE.match(v) for v in colors.values()):
            continue
        colors["surface"] = _fix_surface(colors["surface"])
        # Heading must read on the header band: weak contrast → replace with
        # plain readable text for that band.
        if abs(_lum(colors["heading"]) - _lum(colors["header"])) < 0.3:
            colors["heading"] = "#ffffff" if _lum(colors["header"]) <= 0.6 else "#0a0a0a"
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(p.get("name") or "Palette")).strip()[:40]
        out.append({"name": name or "Palette", **colors})
        if len(out) >= 3:
            break
    return out


def _parse_response(text: str) -> dict | None:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        out = json.loads(s)
    except (ValueError, TypeError):
        return None
    return out if isinstance(out, dict) else None


async def generate_theme_palettes(
    *, account_id: int, logo_bytes: bytes | None,
    wishes: str = "", seed_color: str = "",
) -> list[dict]:
    """Vision (logo) or text-only (no logo) → up to 3 sanitized palettes.

    Best-effort: any failure returns [] and the recruiter keeps the manual
    pickers.  Never raises.
    """
    wishes_txt = re.sub(r"[\x00-\x1f\x7f]", "", wishes or "").strip()[:300] or "none given"
    if not logo_bytes and seed_color:
        wishes_txt += f"  (no logo available — build around the brand colour {seed_color})"
    prompt = _PROMPT.format(
        logo_note=" whose logo is attached" if logo_bytes else "",
        wishes=wishes_txt,
    )
    try:
        # generate_with_vision falls back to text-only generation internally
        # when image_bytes is empty — one call site covers both paths.
        from capabilities.ai.vision import generate_with_vision
        text, _usage = await generate_with_vision(
            prompt, logo_bytes or b"", system=_SYSTEM,
            account_id=account_id, action="theme_ai",
        )
        parsed = _parse_response(text)
        palettes = _sanitize_palettes(parsed) if parsed else []
        logger.info("theme_ai account=%s candidates=%d", account_id, len(palettes))
        return palettes
    except Exception as e:   # noqa: BLE001 — best-effort by contract
        logger.warning("theme_ai failed account=%s: %s", account_id, e)
        return []
