"""The test layout itself, guarded.

Tests are migrating out of the flat tests/ tree and into the package
that owns them, as a ``tests/`` SUBDIRECTORY.  Three things have to stay
true for that to be safe, and none of them fails loudly on its own:

  1. Every package-owned tests/ dir is COLLECTED.  A probe proved that
     before pytest.ini's testpaths were widened, a test placed in
     features/parking/tests/ was collected by neither ``pytest`` nor
     ``pytest tests/`` — and the build stayed green while the suite
     shrank.  CI pins a census floor for the whole suite; this pins the
     narrower rule that no package's tests can fall outside the paths
     pytest is told to walk.

  2. Every package-owned tests/ dir is a PACKAGE.  pytest imports test
     modules by name, so two same-named files in different packages
     collide at import time without __init__.py.

  3. Tests live in a tests/ SUBDIRECTORY, never loose beside the source.
     This is load-bearing, not style: the structural guards prune by
     DIRECTORY NAME, so a tests/ dir is invisible to them while a flat
     features/<x>/test_foo.py would be scanned, imported and enforced as
     production source — 11 files write warehouse SQL that is legal only
     because they are test code.

  4. Package-owned tests do not SHIP.  .dockerignore's ``tests/`` is
     anchored to the build-context root, so it excluded the root suite
     and nothing else: the moment tests moved next to the code they
     guard, all 274 of them began landing in the production image.  A
     build probe confirmed it — the root marker was excluded, the
     nested one was copied in.  Nothing caught that, which is why this
     rule now exists.
"""

from __future__ import annotations

import ast
import configparser
import re

from tests._repo import REPO

_ROOTS = ("features", "capabilities", "adapters", "interfaces", "infra", "system")


def _package_test_dirs() -> list:
    out = []
    for root in _ROOTS:
        base = REPO / root
        if base.is_dir():
            out += [d for d in base.rglob("tests")
                    if d.is_dir()
                    and "__pycache__" not in d.parts
                    # interfaces/*/node_modules carries 14 tests/ dirs of
                    # its own (zod, redux-toolkit, ...).  They are vendored
                    # JS, not packages of ours, and demanding they sit in
                    # testpaths would fail rule 1 for code we do not own.
                    and "node_modules" not in d.parts]
    return sorted(out)


def _configured_testpaths() -> list[str]:
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "pytest.ini")
    return cfg.get("pytest", "testpaths").split()


def test_every_package_test_dir_is_collected():
    """A tests/ dir outside testpaths is invisible: its tests do not run,
    nothing is skipped, and nothing turns red."""
    configured = _configured_testpaths()
    for d in _package_test_dirs():
        rel = d.relative_to(REPO).as_posix()
        assert any(rel == p or rel.startswith(p + "/") for p in configured), (
            f"{rel} is not covered by pytest.ini testpaths {configured} — "
            "its tests would silently never run"
        )


def test_every_package_test_dir_is_a_package():
    for d in _package_test_dirs():
        assert (d / "__init__.py").is_file(), (
            f"{d.relative_to(REPO)} needs an __init__.py — pytest imports "
            "test modules by name, and same-named files in different "
            "packages collide at import time without one"
        )


def test_no_loose_test_files_beside_package_source():
    """The subdirectory form is what makes package-owned tests invisible
    to the structural guards.  A loose test_*.py is not."""
    loose = []
    for root in _ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        # test_*.py is what pytest collects; conftest.py and *_test.py
        # are the rest of the machinery. A loose conftest beside source
        # hands fixtures to production directories, and a loose
        # *_test.py is a file that LOOKS like a test and is silently
        # never run — both belong inside the package's tests/.
        for pattern in ("test_*.py", "*_test.py", "conftest.py"):
            for py in base.rglob(pattern):
                if "tests" in py.parts or "__pycache__" in py.parts \
                        or "node_modules" in py.parts:
                    continue
                loose.append(py.relative_to(REPO).as_posix())
    assert not loose, (
        "test files sitting loose beside package source:\n  "
        + "\n  ".join(sorted(loose))
        + "\nMove them into that package's tests/ subdirectory — the "
          "guards prune by directory NAME, so a loose file is scanned "
          "and enforced as production code."
    )


def test_package_owned_tests_are_excluded_from_the_image():
    """Docker anchors a bare ``tests/`` to the context root.

    The recursive form is what covers features/<x>/tests/ and its
    siblings; without it the image carries the whole suite.
    """
    patterns = [
        ln.strip() for ln in
        (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert "**/tests/" in patterns, (
        "`**/tests/` is missing from .dockerignore, so every package-owned "
        "tests/ dir ships in the production image.  A bare `tests/` does "
        "NOT cover them — it matches the context root only."
    )


# ── rule 5: a root test must be able to say why it is at the root ──
#
# The four rules above are about STRUCTURE. None of them asks whether a
# file in the root tests/ belongs there, and a single-package test
# filed at the root passes them all in silence — which is how fifteen
# of them accumulated, each placed by someone who judged "the
# consequence crosses layers" and did not notice the TEST does not.
# So the judgment becomes a checkable fact: the file crosses packages
# by what it imports, or it scans the tree, or it says why in a
# `# repo-wide: <why>` line — the same shape as `# test-safe:`, and
# a bare marker is refused for the same reason: the reason is the point.

_LAYERS = ("features", "capabilities", "adapters", "interfaces", "infra",
           "integrations", "system")
_REPO_WIDE = re.compile(r"#\s*repo-wide:\s*(?P<why>\S.*)")
# Reading the tree is what a structural guard does; REPO is the sanctioned
# way to find it (never __file__), and rglob/walk are the only calls that
# mean "every file", not "a fixture file".
_SCAN_ATTRS = {"rglob", "walk"}


def _package_of(module: str):
    """features.kpi.service -> features/kpi; adapters.storage.x ->
    adapters/storage; infra.cache -> infra. None for anything else."""
    parts = module.split(".")
    if parts[0] not in _LAYERS:
        return None
    if parts[0] in ("infra", "integrations") or len(parts) == 1:
        return parts[0]
    return "/".join(parts[:2])


def _root_test_verdict(path):
    src = path.read_text()
    tree = ast.parse(src)
    pkgs, scans = set(), False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                pk = _package_of(a.name)
                if pk:
                    pkgs.add(pk)
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module == "tests._repo":
                scans = True
            pk = _package_of(n.module)
            if pk:
                pkgs.add(pk)
        elif isinstance(n, ast.Attribute) and n.attr in _SCAN_ATTRS:
            scans = True
    if len(pkgs) >= 2:
        return "crosses", sorted(pkgs)
    if scans:
        return "scans", None
    ann = _REPO_WIDE.search("\n".join(src.splitlines()[:8]))
    if ann:
        return "annotated", ann.group("why")
    return "single", (next(iter(pkgs)) if pkgs else None)


def test_a_root_test_can_say_why_it_is_at_the_root():
    """Every file in the root tests/ crosses packages, scans the tree,
    or carries `# repo-wide: <why>`. Anything else has a home."""
    misplaced = []
    for py in sorted((REPO / "tests").glob("test_*.py")):
        verdict, detail = _root_test_verdict(py)
        if verdict != "single":
            continue
        if detail:
            home = f"{detail}/tests/"
        else:
            home = ("the package whose methods it drives through the `db` "
                    "fixture — adapters/storage/tests/ for storage mixins")
        misplaced.append(f"{py.name}  ->  {home}")
    assert not misplaced, (
        "root tests/ files that belong to one package:\n  "
        + "\n  ".join(misplaced)
        + "\nA root test crosses packages (by what it IMPORTS, not by what "
          "its docstring says the consequence is), or scans the tree via "
          "tests._repo.REPO, or says why in `# repo-wide: <why>` within its "
          "first lines. Otherwise it lives with the package that owns the "
          "rule it states."
    )
