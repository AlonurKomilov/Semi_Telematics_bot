"""The repository root, resolved by SENTINEL rather than by depth.

Every structural guard in this suite walks the tree from a root, and
every one of them computed that root by counting directory levels up
from ``__file__`` — ``Path(__file__).resolve().parent.parent``.  That is
correct only while the file stays exactly two levels deep.

The failure mode is the dangerous one.  Move such a file one level and
the root silently becomes the WRONG directory: the guard then walks a
tree containing no source files, finds no violations, and **passes**.
Green, not red — an enforcement layer quietly demoted to decoration,
with no signal that it happened.

This is not hypothetical here.  ``tests/test_project_root_depth.py``
exists precisely because three routers miscounted their own
``dirname()`` chains and resolved to the directory ABOVE the repo.

Anchoring on a file that only the repository root contains removes the
assumption entirely: depth-independent, and immune to this move and to
whatever the layout becomes next.
"""

from __future__ import annotations

from pathlib import Path

# pytest.ini is a good sentinel: it must exist for the suite to run at
# all (pytest reads testpaths/asyncio_mode from it), it lives only at
# the root, and it is not something a subdirectory would ever acquire.
_SENTINEL = "pytest.ini"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward until the directory holding the sentinel is found."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _SENTINEL).is_file():
            return candidate
    raise RuntimeError(
        f"repository root not found: no {_SENTINEL!r} in {here} or any parent. "
        "The guards resolve their scan root through this helper, so they "
        "must fail loudly here rather than silently scan nothing."
    )


REPO = find_repo_root()


def scanned(items, what: str, minimum: int = 1):
    """Assert a tree walk actually found something, then return it.

    The companion to the sentinel root.  A guard that walks the wrong
    directory finds zero files and reports success; so does a guard
    whose glob pattern stopped matching after a rename.  Both are
    indistinguishable from "no violations" unless the count itself is
    asserted.

    Usage:  for path in scanned(REPO.glob("features/*/router.py"), "routers"):
    """
    items = list(items)
    assert len(items) >= minimum, (
        f"guard scanned {len(items)} {what} (expected at least {minimum}) — "
        f"the scan root or pattern is wrong, so this guard is currently "
        f"enforcing NOTHING. Root in use: {REPO}"
    )
    return items

# ── what is test code ─────────────────────────────────────────────
#
# Backend tests are migrating INTO the package that owns them, as a
# `tests/` SUBDIRECTORY — features/<x>/tests/, capabilities/<x>/tests/ —
# never as loose test_*.py beside the source.  That form is load-bearing:
# the structural guards prune by DIRECTORY NAME, so a `tests` directory
# is already invisible to them, while a flat features/<x>/test_foo.py
# would be scanned, imported and enforced as production source.
#
# Several guards used to spell "this is test code" as
# `rel.startswith("tests/")`, which is true only while every test lives
# in the one top-level directory.  One place answers it now, so the
# guards cannot drift into disagreeing about what they are auditing.

_TEST_DIR = "tests"


def is_test_path(rel) -> bool:
    """True when a repo-relative path is test code, wherever it lives.

    Covers the top-level tests/ tree AND any package-owned tests/
    subdirectory.  Takes a str or a Path; compares path PARTS, never a
    substring, so a source file called `latest/thing.py` or a package
    named `contests` is not mistaken for a test.
    """
    parts = getattr(rel, "parts", None)
    if parts is None:
        parts = str(rel).replace("\\", "/").split("/")
    return _TEST_DIR in parts
