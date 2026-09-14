"""Every deprecated ``capabilities.platform.<x>`` / ``capabilities.security``
path names the SAME module object as its ``system.<x>`` home — never a
copy. A copy carries its own module state (caches, registries), and a
hold cleared through one spelling stays cached under the other."""

import importlib
import pkgutil

import pytest

import system

PACKAGES = ("security", "capacity", "market_intel", "part_directory",
            "service_task_library", "service_assembly_library", "vendor_directory")
OLD = {"security": "capabilities.security"}


def _modules():
    for pkg in PACKAGES:
        new_pkg = importlib.import_module(f"system.{pkg}")
        old_root = OLD.get(pkg, f"capabilities.platform.{pkg}")
        # The PACKAGE objects are deliberately distinct (see the alias
        # __init__ docstrings): only the modules, which carry state, must
        # be one object.
        for m in pkgutil.iter_modules(new_pkg.__path__):
            if m.name == "tests" or m.ispkg:
                continue
            yield f"{old_root}.{m.name}", f"system.{pkg}.{m.name}"
    yield "capabilities.platform.watchdog", "system.watchdog"


@pytest.mark.parametrize("old,new", list(_modules()))
def test_the_deprecated_path_is_the_same_object(old, new):
    assert importlib.import_module(old) is importlib.import_module(new), f"{old} is a copy, not an alias"
