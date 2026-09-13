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
BTNCLS_BUDGET = {
    "ServiceTaskLibrary.tsx",
    "MarketIntel.tsx",
    "PartsDirectory.tsx",
    "ServiceAssemblies.tsx",
}

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
