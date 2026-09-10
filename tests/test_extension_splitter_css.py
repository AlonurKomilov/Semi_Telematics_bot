"""The resize handle's class has a rule, and the rule has hover.

A cross-file contract neither package can check alone.  The component
lives in TypeScript and writes `className="splitter"`; the appearance
lives in `index.css`.  The extension's own vitest suite cannot read the
CSS — this project's Vite config processes stylesheets, so a `?raw`
import hands the test an empty string (probed, not assumed) — and the
CSS has no test runner of its own.

Which is exactly what the repo-root suite is for.

The rule it holds: the handle must ANSWER THE POINTER.  A 2px line at
rest is easy to miss and easy to mistake for a border, and the owner
said so on first use — a resize handle nobody can see is a feature
nobody finds.  Hover and keyboard focus both get the answer, because
somebody nudging it with the arrow keys needs to see which line is
listening.
"""
from __future__ import annotations

import pytest

from tests._repo import REPO

TSX = REPO / "interfaces/browser_extension/src/shell/Splitter.tsx"
CSS = REPO / "interfaces/browser_extension/src/index.css"


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text()


def test_the_component_writes_the_class():
    assert 'className="splitter"' in TSX.read_text()


@pytest.mark.parametrize("rule", [
    ".splitter > i",                 # the line itself
    ".splitter:hover > i",           # …under the pointer
    ".splitter:focus-visible > i",   # …and under the keyboard
])
def test_every_state_the_handle_needs_has_a_rule(css, rule):
    assert rule in css, f"index.css has no rule for {rule!r}"


def test_the_resting_line_is_quieter_than_the_answered_one(css):
    """Otherwise "answering" says nothing.  At rest the line wears the
    border colour and 2px; touched, it thickens and takes the accent."""
    body = css[css.index(".splitter > i"):]
    assert "var(--border)" in body[:body.index("}")]
    hover = css[css.index(".splitter:hover > i"):]
    assert "var(--primary)" in hover[:hover.index("}")]
