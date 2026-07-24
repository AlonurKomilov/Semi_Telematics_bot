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

ROOT = Path(__file__).resolve().parent.parent

# Backend code that runs in production.  Frontend has its own linters;
# scripts/ and tests/ are not worth gating.
PACKAGES = ("capabilities", "features", "adapters", "infra", "interfaces/api", "interfaces/bot")


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
        for py in sorted((ROOT / pkg).rglob("*.py")):
            pyflakes_api.check(py.read_text(encoding="utf-8"), str(py.relative_to(ROOT)), collector)
    assert not collector.findings, (
        "Undefined names in backend code (each is a NameError waiting for "
        "its branch to execute):\n" + "\n".join(collector.findings)
    )
