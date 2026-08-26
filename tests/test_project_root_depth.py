"""Every ``_PROJECT_ROOT`` must actually point at the project root.

These constants are built by chaining ``os.path.dirname`` up from
``__file__``, so the correct number of calls depends on how deep the file
sits.  Get it wrong by one and nothing raises: the constant is simply a
different directory, and every path built from it silently misses.

That is not hypothetical.  Three routers — parking, cameras, scorecards —
each chained FOUR calls from ``<root>/features/<x>/router.py``, which needs
three.  The result was ``/home/abcdev/projects`` (the project's PARENT), so:

  * every stored-file lookup failed.  ``resolve_disk_path`` searched
    ``<parent>/data/userdata/...`` instead of ``<root>/data/userdata/...``
    and returned None, and the route raised 404 "Map image file not
    found" for EVERY parking event, from the day it shipped.  The
    frontend rendered a permanent "Loading map..." so it never surfaced.
  * the path-traversal guard widened.  Those routes check
    ``realpath(file).startswith(realpath(_PROJECT_ROOT))`` before serving;
    with the root one level too high, that check admitted anything under
    the parent directory, including sibling projects.

A static test on purpose: it reads the source rather than importing the
modules, so it cannot be defeated by an import cycle (importing
``features.parking.router`` directly does hit one) and it costs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted
from tests._repo import scanned  # a guard that scans nothing must fail

REPO_ROOT = _REPO

# Matches `_PROJECT_ROOT = os.path.dirname(os.path.dirname(...))` and the
# multi-line variant, capturing the whole right-hand side.
_ASSIGN = re.compile(
    r"_PROJECT_ROOT\s*=\s*((?:os\.path\.dirname\s*\(\s*)+os\.path\.abspath\s*\(\s*__file__\s*\)[\s)]*)",
)

SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv"}


def _python_files() -> list[Path]:
    out = []
    for path in scanned(REPO_ROOT.rglob("*.py"), "repo sources"):
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        out.append(path)
    return out


def test_every_project_root_constant_resolves_to_the_repo_root():
    checked = 0
    wrong: list[str] = []

    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "_PROJECT_ROOT" not in text:
            continue
        for match in _ASSIGN.finditer(text):
            depth = match.group(1).count("os.path.dirname")
            checked += 1
            # depth-1 climbs above the file's own directory; the file's
            # directory is itself `len(relative.parts) - 1` below the root.
            file_depth = len(path.relative_to(REPO_ROOT).parts) - 1
            needed = file_depth + 1
            if depth != needed:
                rel = path.relative_to(REPO_ROOT)
                wrong.append(
                    f"{rel}: chains {depth} dirname call(s) but sits {file_depth} "
                    f"director{'y' if file_depth == 1 else 'ies'} below the root, "
                    f"so it needs {needed}"
                )

    assert checked, "found no _PROJECT_ROOT assignments — the regex has drifted"
    assert not wrong, (
        "These _PROJECT_ROOT constants do not point at the project root:\n  "
        + "\n  ".join(wrong)
        + "\n\nA wrong root does not raise — it silently relocates every path "
        "built from it, and weakens any traversal guard that uses it."
    )
