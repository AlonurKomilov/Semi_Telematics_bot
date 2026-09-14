"""``capabilities.security.<m>`` and ``system.security.<m>`` are ONE
module object, not two copies. A copy would carry its own caches: a hold
cleared through one name would still be cached under the other, and the
person would be refused or admitted depending on which spelling the
caller happened to use."""

import importlib

import pytest

MODULES = ("detector", "owner_notice", "quarantine", "recorder", "retention", "watch")


@pytest.mark.parametrize("name", MODULES)
def test_the_deprecated_path_is_the_same_object(name):
    old = importlib.import_module(f"capabilities.security.{name}")
    new = importlib.import_module(f"system.security.{name}")
    assert old is new, f"capabilities.security.{name} is a copy, not an alias"


def test_the_caches_are_shared_not_duplicated():
    import capabilities.security.quarantine as old
    import system.security.quarantine as new
    old.forget(); new._cache[123456] = (True, 1e18)
    assert old._cache is new._cache and old._cache[123456][0] is True
    new.forget(123456)
    assert 123456 not in old._cache
