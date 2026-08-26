"""Undefined-name guard for the backend (pyflakes F821).

Born from a production crasher the 2026-07-24 alerts architecture audit
found: ``_try_post_to_topic`` forwarded ``_group_text_plain`` /
``_include_ai_in_group`` — locals of ``send_alert``, invisible in its own
scope — so every group-routed alert died with ``NameError`` before the DM
fallback could run.  The same sweep caught ``capabilities/reporting``
raising an un-imported ``HTTPException`` (500 instead of 404/422).

Python only resolves names at call time, so nothing else in the test
suite catches a wrong-scope reference until that exact branch executes.
This test runs pyflakes' undefined-name check over the backend packages
so the whole class of bug fails CI instead of production.
"""
from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip(
    "pyflakes.api", reason="pyflakes not installed (dev-only guard)"
)
from pyflakes.messages import UndefinedName  # noqa: E402
from pyflakes.reporter import Reporter  # noqa: E402
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted
from tests._repo import scanned  # a guard that scans nothing must fail

ROOT = _REPO

# Backend code that runs in production, PLUS the test suite itself.
# Frontend has its own linters; scripts/ stays out.
#
# tests/ used to be excluded as "not worth gating".  Evidence says
# otherwise: tests/test_config_endpoint_convention.py carried an
# undefined name for however long inside its own FAILURE MESSAGE, so the
# guard passed while green and would have raised NameError instead of
# naming the offending endpoint at the exact moment someone needed to
# read it.  pyflakes finds that in milliseconds.  Including tests/ costs
# nothing — the tree is at zero violations — and it becomes DELIBERATE
# rather than accidental as tests migrate into features/ and
# capabilities/, where this walk would have picked them up anyway.
PACKAGES = ("capabilities", "features", "adapters", "infra",
            "interfaces/api", "interfaces/bot", "tests")


class _Collector(Reporter):
    """Collect UndefinedName findings; ignore every other pyflakes class
    (unused imports etc. are style, not crashers — not this test's job)."""

    def __init__(self) -> None:
        self.findings: list[str] = []

    def flake(self, message) -> None:  # pyflakes callback
        if isinstance(message, UndefinedName):
            self.findings.append(str(message))

    # Syntax errors would already fail imports elsewhere; report anyway.
    def syntaxError(self, filename, msg, lineno, offset, text) -> None:
        self.findings.append(f"{filename}:{lineno}: syntax error: {msg}")

    def unexpectedError(self, filename, msg) -> None:
        self.findings.append(f"{filename}: pyflakes error: {msg}")


def test_no_undefined_names_in_backend() -> None:
    collector = _Collector()
    for pkg in PACKAGES:
        for py in scanned(sorted((ROOT / pkg).rglob("*.py")), f"{pkg} sources"):
            pyflakes_api.check(py.read_text(encoding="utf-8"), str(py.relative_to(ROOT)), collector)
    assert not collector.findings, (
        "Undefined names in backend code (each is a NameError waiting for "
        "its branch to execute):\n" + "\n".join(collector.findings)
    )
