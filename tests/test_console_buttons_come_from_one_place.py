"""The operator console has one button, and weight is what it says.

Five pages had each declared their own ``btnCls`` — four identical, one
quietly wider — and eight more filled-button class strings were written
inline. Nothing was wrong in any single one of them. What was missing
was a way to say "this one matters more than that one": on the Plans
page the act that reaches every account on a plan looked exactly like
the one that re-reads a checklist, and the owner said so.

``interfaces/system_dashboard/src/components/ui/Button.tsx`` is the
answer and ``interfaces/system_dashboard/design.md`` is the rule. This
guard keeps both true, because a rule with no guard decays — and the
console has no JS test runner of its own (no vitest script, no
@testing-library), so the check lives here, where CI already looks.
"""

from __future__ import annotations

import re

from tests._repo import REPO

CONSOLE = REPO / "interfaces" / "system_dashboard" / "src"
BUTTON = CONSOLE / "components" / "ui" / "Button.tsx"

#: Files that still declare a local button class, with the count frozen
#: at what existed when the primitive landed.  A ratchet, not a pass:
#: these may fall to zero, never rise.  (Plans.tsx was the first to
#: adopt the primitive and is deliberately absent.)
BTNCLS_BUDGET: set[str] = set()   # every page now imports the primitive

#: Meaning goes through the four tokens in tailwind.config.js.  Inside
#: the shared primitive there is no excuse at all.
RAW_PALETTE = re.compile(
    r"\b(?:text|bg|border|ring)-(?:rose|emerald|amber|yellow|red|green|blue|indigo)-\d")


def test_the_primitive_exists_and_every_variant_looks_different():
    src = BUTTON.read_text()
    variants = dict(re.findall(r"^  (\w+):\s+'([^']+)',$", src, re.M))
    assert set(variants) >= {"primary", "secondary", "warn", "danger", "ghost"}, (
        f"a variant went missing: {sorted(variants)}")
    looks = list(variants.values())
    assert len(set(looks)) == len(looks), (
        "two variants render identically — the whole point is that weight is legible")


def test_only_the_primary_is_filled():
    """A filled secondary is how a page ends up with three primaries and
    no hierarchy at all."""
    src = BUTTON.read_text()
    variants = dict(re.findall(r"^  (\w+):\s+'([^']+)',$", src, re.M))
    assert "bg-accent" in variants["primary"]
    for name, classes in variants.items():
        if name == "primary":
            continue
        assert not re.search(r"\bbg-accent\b", classes), (
            f"{name} is filled like the primary — {classes!r}")


def test_the_primitive_takes_colour_from_the_tokens():
    offending = RAW_PALETTE.findall(BUTTON.read_text())
    assert not offending, (
        f"the shared button reaches past the tokens: {sorted(set(offending))}")


def test_a_visible_focus_ring_survives():
    assert "focus-visible:ring" in BUTTON.read_text(), (
        "an operator who navigates by keyboard loses the button entirely")


def test_every_overlay_is_the_dialog_primitive():
    """A hand-rolled backdrop has no focus trap, no Escape, no aria-modal
    and no scroll lock — Tab walks out of it and keeps going through the
    page underneath, which is still there and still focusable."""
    dialog = CONSOLE / "components" / "ui" / "Dialog.tsx"
    src = dialog.read_text()
    for required in ('role="dialog"', 'aria-modal="true"', "'Escape'", "overflow"):
        assert required in src, f"the Dialog primitive lost {required}"
    assert "e.key !== 'Tab'" in src, "the focus trap is gone"

    offenders = [
        p.relative_to(CONSOLE).as_posix()
        for p in CONSOLE.rglob("*.tsx")
        if "fixed inset-0" in p.read_text() and p.name != "Dialog.tsx"
    ]
    assert not offenders, (
        "these build their own modal instead of using components/ui/Dialog:\n    "
        + "\n    ".join(sorted(offenders)))


def test_one_input_style_and_one_card():
    locals_ = {p.name for p in CONSOLE.rglob("*.tsx") if "const inputCls =" in p.read_text()}
    assert not locals_, f"a page re-declared the input style: {sorted(locals_)}"


def test_per_operator_state_goes_through_the_prefs_module():
    """Raw storage calls scattered through pages is how a key gets renamed
    by someone who cannot see the other three call sites."""
    prefs = CONSOLE / "lib" / "prefs.ts"
    assert prefs.exists(), "the preferences module is gone"
    offenders = [
        p.relative_to(CONSOLE).as_posix()
        for p in CONSOLE.rglob("*.tsx")
        if re.search(r"\blocalStorage\b|\bsessionStorage\b", p.read_text())
    ]
    assert not offenders, (
        "these touch storage directly instead of lib/prefs.ts:\n    "
        + "\n    ".join(sorted(offenders)))


#: Two categories the audit found and this pass did NOT fix, frozen at
#: today's counts so they can only fall.  Raw palette classes need a
#: shade-by-shade judgement on pages nobody has opened in a browser
#: (a token is one hex, `rose-300` and `rose-500/10` are a contrast
#: PAIR), and the native tooltips need a themed Tooltip component that
#: does not exist yet.  A ratchet is not a fix; it is a promise that it
#: will not get worse while it waits.
RAW_PALETTE_BUDGET = 154
TITLE_TOOLTIP_BUDGET = 44


def _count(pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(len(rx.findall(p.read_text())) for p in CONSOLE.rglob("*.tsx"))


def test_raw_palette_and_native_tooltips_only_go_down():
    raw = _count(r"\b(?:text|bg|border)-(?:rose|emerald|amber|yellow)-\d")
    # a Dialog's `title` is its heading (components/ui/Dialog.tsx), not a
    # native tooltip — the counter used to charge every dialog one tooltip
    tips = _count(r"title=\{|title=\"") - _count(r"<Dialog\s+title=")
    assert raw <= RAW_PALETTE_BUDGET, (
        f"{raw} raw palette classes, budget {RAW_PALETTE_BUDGET} — "
        "meaning goes through accent/danger/warn/ok (design.md)")
    assert tips <= TITLE_TOOLTIP_BUDGET, (
        f"{tips} native title tooltips, budget {TITLE_TOOLTIP_BUDGET} — "
        "they are unthemed and invisible on touch")


def test_no_page_grows_a_new_local_button_class():
    found = {p.name for p in CONSOLE.rglob("*.tsx") if "const btnCls" in p.read_text()}
    new = found - BTNCLS_BUDGET
    assert not new, (
        "these pages declare their own button instead of importing the primitive:\n    "
        + "\n    ".join(sorted(new))
        + "\n\nUse `import { Button } from '../components/ui/Button'` — see "
          "interfaces/system_dashboard/design.md."
    )
    # and the budget itself may only shrink
    assert found <= BTNCLS_BUDGET


def test_plans_page_uses_the_primitive_for_its_primary_action():
    """The page the owner was looking at when he asked the question."""
    plans = (CONSOLE / "pages" / "Plans.tsx").read_text()
    assert "from '../components/ui/Button'" in plans
    assert "const btnCls" not in plans
    assert "variant={armed ? 'primary' : 'ghost'}" in plans, (
        "Save/Create Stripe price must be filled when there is something to do")
