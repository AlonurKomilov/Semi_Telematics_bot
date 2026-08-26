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
"""

from __future__ import annotations

import configparser

from tests._repo import REPO

_ROOTS = ("features", "capabilities")


def _package_test_dirs() -> list:
    out = []
    for root in _ROOTS:
        base = REPO / root
        if base.is_dir():
            out += [d for d in base.rglob("tests")
                    if d.is_dir() and "__pycache__" not in d.parts]
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
        for py in base.rglob("test_*.py"):
            if "tests" in py.parts or "__pycache__" in py.parts:
                continue
            loose.append(py.relative_to(REPO).as_posix())
    assert not loose, (
        "test files sitting loose beside package source:\n  "
        + "\n  ".join(sorted(loose))
        + "\nMove them into that package's tests/ subdirectory — the "
          "guards prune by directory NAME, so a loose file is scanned "
          "and enforced as production code."
    )
