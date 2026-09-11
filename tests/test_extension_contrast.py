"""The panel's colours, measured rather than eyeballed.

Two of this file's numbers were wrong when a human wrote them down: a
comment claimed an edge token was 3.1:1 when it was 2.22:1, and white on
the primary fill was assumed to be fine at 3.68:1.  Contrast is
arithmetic, so it belongs in a test and not in a sentence.

The pairs are declared, because no parser can tell from a stylesheet
which colour ends up on which ground — but every RATIO here is computed
from the tokens as they are in `index.css` right now, so changing a
token changes the verdict.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

CSS = REPO / "interfaces/browser_extension/src/index.css"

# WCAG 2.2
AA_TEXT = 4.5          # 1.4.3, text under 18.66px / bold 24px
AA_NON_TEXT = 3.0      # 1.4.11, anything that identifies a control


def _tokens() -> dict[str, str]:
    css = CSS.read_text(encoding="utf-8")
    root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    # Comments in this block quote ratios like "3.1:1" — strip them, or a
    # hex inside prose would be read as a declaration.
    root = re.sub(r"/\*.*?\*/", "", root, flags=re.S)
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})", root))


def _lum(hex_colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _c(name: str) -> str:
    """A token by name, or a literal hex passed straight through."""
    return name if name.startswith("#") else _tokens()[name]


# (foreground, background, floor, what wears it)
PAIRS = [
    ("fg",      "bg",   AA_TEXT, "body text"),
    ("fg",      "card", AA_TEXT, "text on a card"),
    ("muted",   "bg",   AA_TEXT, "captions, at 11px and 12px — never large text"),
    ("muted",   "card", AA_TEXT, "row captions"),
    ("primary", "card", AA_TEXT, ".link, which is TEXT and nothing else"),
    ("danger",  "card", AA_TEXT, "error lines"),
    ("warn",    "card", AA_TEXT, "the stale-position line"),
    ("ok",      "card", AA_TEXT, "settled counts"),
    ("#ffffff", "primary-fill", AA_TEXT, ".btn.primary 13px, .chip.on and .avatar 12px"),
    ("#ffffff", "#2f6fd0",      AA_TEXT, "the same three on hover — the half nobody checks"),
    ("edge",         "card", AA_NON_TEXT, "a control's own edge, 1.4.11"),
    ("edge",         "bg",   AA_NON_TEXT, "a control's own edge, 1.4.11"),
    ("primary-fill", "card", AA_NON_TEXT, "the filled button's boundary against the card"),
    ("primary-fill", "bg",   AA_NON_TEXT, "the filled button's boundary against the panel"),
]


@pytest.mark.parametrize("fg,bg,floor,worn_by", PAIRS,
                         ids=[f"{a}-on-{b}" for a, b, _, _ in PAIRS])
def test_pair_clears_its_floor(fg: str, bg: str, floor: float, worn_by: str) -> None:
    got = contrast(_c(fg), _c(bg))
    assert got >= floor, (
        f"{fg} on {bg} is {got:.2f}:1, below {floor} — worn by {worn_by}"
    )


OVERLAY = REPO / "interfaces/browser_extension/src/content/mapsOverlay.ts"

# Google paints the land behind our card: #f2efe9 on the default map,
# #3c4043 on its dark one.  A translucent ground has TWO ratios, and the
# control has to clear its floor on both.
MAP_GROUNDS = {"light map": "#f2efe9", "dark map": "#3c4043"}


def _composite(fg: str, alpha: float, bg: str) -> str:
    f = [int(fg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(alpha * x + (1 - alpha) * y):02x}"
                         for x, y in zip(f, b))


def _overlay_const(name: str) -> str:
    m = re.search(rf"const {name} = '(#[0-9a-fA-F]{{6}})'", OVERLAY.read_text(encoding="utf-8"))
    assert m, f"{name} is gone from mapsOverlay.ts"
    return m.group(1)


@pytest.mark.parametrize("map_name", list(MAP_GROUNDS))
def test_the_overlay_switch_is_visible_when_it_is_off(map_name: str) -> None:
    """The control saying the vehicles are HIDDEN was the invisible one.

    This surface is drawn onto somebody else's page, so it carries its
    own literals and cannot inherit --edge.  The off track was #4b5563:
    2.02:1 against its own pill over a light map.
    """
    pill = _composite("#11141a", 0.92, MAP_GROUNDS[map_name])
    track = _overlay_const("TRACK_OFF")
    assert contrast(track, pill) >= AA_NON_TEXT, (
        f"the off track is {contrast(track, pill):.2f}:1 on the pill over the "
        f"{map_name} — the switch disappears in exactly the state that needs "
        f"a press"
    )
    # …and the knob has to stay legible ON that track, or the state moves
    # from invisible to ambiguous.
    assert contrast("#ffffff", track) >= AA_NON_TEXT


#: Google paints three grounds under this card within one page.
GOOGLE_GROUNDS = {"dark land": "#212121", "roads": "#3c4043", "light theme": "#f2efe9"}


@pytest.mark.parametrize("ground", list(GOOGLE_GROUNDS))
def test_the_overlay_card_keeps_an_edge_on_every_google_ground(ground: str) -> None:
    """The card's boundary is a PAIR of rings, and the pair is the point.

    No single translucent ring clears 3:1 on all three grounds Google
    paints within one page, so the card wears both: where the page is
    dark the inner light ring carries the edge, where it is light the
    outer dark one does.  This asserts at least one of them always does.

    It is checked here rather than trusted because this file has form.
    A comment once claimed a two-part shadow the edit had never made —
    and the whole style block, rings included, spent time never
    rendering at all: a missing `+` let ASI close the cssText assignment
    early, and the card was transparent on somebody else's map.  The
    numbers below were first written against a card nobody could see.
    """
    src = OVERLAY.read_text(encoding="utf-8")
    assert "box-shadow:0 0 0 1px rgba(255,255,255,.65),0 0 0 2px rgba(0,0,0,.65)" in src, (
        "the two-ring boundary is gone or its alphas changed — re-measure"
    )
    page = GOOGLE_GROUNDS[ground]
    inner = _composite("#ffffff", 0.65, page)
    outer = _composite("#000000", 0.65, page)
    best = max(contrast(inner, page), contrast(outer, page))
    assert best >= AA_NON_TEXT, (
        f"on Google's {ground} neither ring reaches {AA_NON_TEXT}:1 "
        f"(best {best:.2f}) — the card has no visible boundary there"
    )


def test_every_piece_of_the_card_style_is_joined_to_the_one_above_it() -> None:
    """The bug that hid all of the above, checked as the chain it is.

    `card.style.cssText` is built from string literals spread over a
    dozen lines with comments between them.  A continuation line that
    does not hang off a `+` does not extend the assignment — a string
    literal cannot follow a string literal, so ASI closes the statement
    and the rest becomes an expression nobody reads.  That is what
    shipped: the card rendered with position and width and nothing else,
    transparent on google.com/maps.

    `no-unused-expressions` in the extension's eslint config is the
    first guard and points straight at the line.  This is the second,
    and it states the invariant in the terms this file cares about —
    the declarations that decide contrast must reach the element.

    (The first attempt at this test split the source on the first
    ";\\n" and compared strings; it passed in BOTH states, which is no
    test at all.  Checking the join is what distinguishes them.)
    """
    src = OVERLAY.read_text(encoding="utf-8")
    body = src.split("card.style.cssText =", 1)[1]
    lines = body.split("\n")

    previous = ""          # the last line that was not a comment
    pieces = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if previous and line.startswith("'"):
            # A continuation that opens with a quote must hang off the `+`
            # of the line before it.  One that opens with `+` already does.
            assert previous.endswith("+"), (
                "a piece of the card's style does not hang off a `+`, so it "
                f"never reaches the element:\n  after: {previous}\n  line:  {line}"
            )
        if previous and (line.startswith("'") or line.startswith("+")):
            pieces += 1
        previous = line
        if line.endswith(";"):
            break

    # Without this the test would pass on a one-line assignment, which is
    # a state it says nothing about.
    assert pieces >= 3, "the card style is no longer a multi-line chain — re-read this test"


def test_the_separator_stays_a_separator() -> None:
    """--border is deliberately below every floor, and must not be reached for.

    It divides rows, where a whisper is correct.  The bug this records is
    the opposite of a contrast failure: --border was being used as the
    EDGE of controls, which is what --edge exists for.
    """
    t = _tokens()
    assert contrast(t["border"], t["card"]) < AA_NON_TEXT
    css = CSS.read_text(encoding="utf-8")
    for rule in (".btn {", ".input {", ".chip {"):
        body = css[css.index(rule):css.index("}", css.index(rule))]
        assert "var(--edge)" in body, f"{rule} must take its edge from --edge"
        assert "border:1px solid var(--border)" not in body
