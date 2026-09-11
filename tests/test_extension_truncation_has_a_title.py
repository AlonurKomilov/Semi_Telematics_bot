"""Text that ellipsises must leave a way to read the rest of it.

The panel is 320px at its floor and every identity on it is a unit
number followed by a company.  Those lines are the thing somebody is
scanning for, and they are the first to be cut — so a row that truncates
with no `title` hides exactly the characters the person came to read,
with no gesture that recovers them.

This is a SWEEP rather than a list of known elements because the defect
came back: one pass added the tooltip to a vehicle's name and left the
category beside it shrinkable with none, which moved the hole one
element to the right instead of closing it.  A guard that names its
targets can only ever catch the targets somebody remembered.

Every exception is declared here, with its reason, and a file that stops
needing its exception fails too — a stale waiver is a hole that reads
like a decision.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

SRC = REPO / "interfaces/browser_extension/src"

# path suffix -> why this one is allowed to truncate without a title
WAIVED = {
    "shell/FeatureMenu.tsx": (
        "the button around it already carries title='Switch feature', a "
        "second tooltip on the text inside would shadow it, and what "
        "truncates is a short enum the menu below spells out in full"
    ),
}

NAMES_IT = ("title=", "aria-label=", "aria-labelledby=")


def _truncating_elements() -> list[tuple[str, int, str]]:
    """Every element whose style ellipsises, as (path, line, opening tag)."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"textOverflow:\s*['\"]ellipsis['\"]", text):
            # Walk back to this element's `<`, forward to the end of its
            # opening tag.  Attributes carry `{...}` with `>` inside them
            # only in arrow functions, which none of these elements have.
            start = text.rfind("<", 0, m.start())
            end = text.find(">", m.end())
            if start == -1 or end == -1:
                continue
            found.append((
                str(path.relative_to(SRC)),
                text.count("\n", 0, start) + 1,
                " ".join(text[start:end + 1].split()),
            ))
    return found


def test_the_sweep_finds_something() -> None:
    """A regex that matches nothing would pass this file silently."""
    assert len(_truncating_elements()) >= 8


@pytest.mark.parametrize("rel,line,tag", _truncating_elements(),
                         ids=lambda v: str(v) if not isinstance(v, str) or len(v) < 40 else v[:40])
def test_truncating_text_offers_the_whole_value(rel: str, line: int, tag: str) -> None:
    if rel in WAIVED:
        pytest.skip(f"{rel}: {WAIVED[rel]}")
    assert any(k in tag for k in NAMES_IT), (
        f"{rel}:{line} ellipsises with no title, aria-label or "
        f"aria-labelledby — the cut characters are unreachable:\n  {tag}"
    )


def test_no_waiver_outlives_its_reason() -> None:
    """A file that no longer truncates does not get to keep its waiver."""
    truncating = {rel for rel, _, _ in _truncating_elements()}
    stale = set(WAIVED) - truncating
    assert not stale, f"waived but no longer truncating: {sorted(stale)}"
