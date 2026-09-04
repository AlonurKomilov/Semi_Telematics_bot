"""Every client draws the SAME provider mark.

A provider's logo is one file — ``capabilities/integrations/<provider>/
assets/logo.svg`` — but each client must INLINE it (the paths are
``currentColor``, and an image element renders in its own document where
that resolves to black).  So the artwork is copied into each app's
source tree by ``scripts/gen_provider_marks.py``, and a copy nobody
checks is a copy that drifts: one surface quietly starts drawing a
slightly different logo than the next.

This is the check, in the language the generator is written in, so it
covers every client at once rather than once per front-end test runner.
"""
from __future__ import annotations

import importlib.util
import sys

from tests._repo import REPO

_spec = importlib.util.spec_from_file_location(
    "gen_provider_marks", REPO / "scripts" / "gen_provider_marks.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_provider_marks"] = gen
_spec.loader.exec_module(gen)


def test_every_client_file_is_what_the_generator_would_write():
    want = gen.render()
    stale = [t for t in gen.TARGETS if not t.is_file() or t.read_text() != want]
    assert not stale, (
        "these generated files no longer match the canonical assets — run "
        "`python3 scripts/gen_provider_marks.py`: "
        + ", ".join(str(t.relative_to(REPO)) for t in stale)
    )


def test_the_generator_reads_a_real_asset_for_every_provider():
    """A provider listed with no asset would silently generate nothing —
    the mark would vanish from every client at once."""
    for provider in gen.PROVIDERS:
        svg = gen.ASSETS / provider / "assets" / "logo.svg"
        assert svg.is_file(), f"{provider} has no {svg.relative_to(REPO)}"


def test_the_marks_inherit_their_colour():
    """``currentColor`` is what makes ONE asset serve the light theme,
    the dark theme and any accent.  A baked hex would need a second file
    per theme, and nobody would keep them in step."""
    art = gen.render()
    assert 'fill="currentColor"' in art
    assert "#" not in art.split("*/", 1)[1], "a colour literal reached the artwork"


def test_a_client_that_is_added_gets_generated_too():
    """The target list is the whole contract: a new front-end that draws
    marks adds itself here, and this test then holds it to the asset."""
    names = [t.name for t in gen.TARGETS]
    assert names and all(n == "providerMarks.tsx" for n in names)
    assert len(set(gen.TARGETS)) == len(gen.TARGETS), "a target is listed twice"
