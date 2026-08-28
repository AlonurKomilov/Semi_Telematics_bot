"""``_available`` is a live verdict, not a boot verdict.

It used to be set once by ``init_redis`` and never touched again, so a
Redis that died AFTER startup left ``is_available()`` returning True for
the life of the process. Every consumer stayed on the Redis branch, each
helper's ``except`` returned its neutral value, and callers read that
neutral value as fact — ``exists()`` False becomes "no flag set", which
downstream is "not in breach", "not revoked", "not yet alerted". The
in-process fallbacks written all over this codebase became unreachable
dead code at precisely the moment they existed for.

These tests drive the flag directly rather than by stopping Redis: the
behaviour under test is the LATCH and its recovery, and a test that
needs a real outage is a test nobody runs.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

import infra.cache as cache


@pytest.fixture(autouse=True)
def _restore_flag():
    """Never leave the module-global latch flipped for the next test."""
    before = (cache._available, cache._last_probe, cache._probe_task)
    yield
    cache._available, cache._last_probe, cache._probe_task = before


class TestTheLatchGoesDown:

    def test_a_connection_error_marks_redis_down(self):
        cache._available = True
        cache._note_down(RedisConnectionError("boom"))
        assert cache._available is False

    def test_a_timeout_marks_redis_down(self):
        """A socket read deadline expiring is the shape a hung-but-
        accepting Redis takes — the case socket_timeout exists for."""
        cache._available = True
        cache._note_down(RedisTimeoutError("slow"))
        assert cache._available is False

    def test_an_os_error_marks_redis_down(self):
        cache._available = True
        cache._note_down(OSError("broken pipe"))
        assert cache._available is False

    def test_a_usage_error_does_NOT_mark_redis_down(self):
        """WRONGTYPE, a decode failure, a bad argument — Redis is fine
        and the CALLER is not. Marking the whole cache down for that
        would be an outage this module invented for itself."""
        cache._available = True
        cache._note_down(ValueError("WRONGTYPE"))
        assert cache._available is True


class TestTheLatchComesBack:

    def test_is_available_schedules_a_probe_when_down(self):
        """The recovery loop has to be closed HERE, because consumers
        branch on this flag and take their fallback — once it is False
        nothing else would ever touch Redis to notice it returned."""
        async def go():
            cache._available = False
            cache._last_probe = 0.0
            cache._probe_task = None
            assert cache.is_available() is False       # stale by design
            assert cache._probe_task is not None, (
                "a down answer must ask whether it is still true")
            await asyncio.sleep(0)
        asyncio.run(go())

    def test_the_probe_task_is_strongly_referenced(self):
        """asyncio keeps only a weak reference to a bare create_task, so
        a fire-and-forget probe can be collected before it runs. It was:
        the probe was scheduled, never executed, and Redis stayed marked
        down while being perfectly healthy."""
        import inspect
        src = inspect.getsource(cache._schedule_reprobe)
        assert "_probe_task =" in src

    def test_the_cooldown_stops_a_probe_stampede(self):
        async def go():
            cache._available = False
            cache._last_probe = 0.0
            cache._probe_task = None
            cache.is_available()
            first = cache._last_probe
            for _ in range(50):
                cache.is_available()
            assert cache._last_probe == first
        asyncio.run(go())

    def test_no_running_loop_is_not_an_error(self):
        """is_available() is sync and called from sync contexts too. It
        must answer, not raise, when there is no loop to schedule on."""
        cache._available = False
        cache._last_probe = 0.0
        cache._probe_task = None
        assert cache.is_available() is False           # no exception


class TestEveryFailurePathReports:

    def test_no_helper_swallows_a_failure_silently(self):
        """Every ``except`` in this module routes through _note_down.
        The one that mattered most was ``exists()`` — the trigger breach
        flags, the watchdog dedup and the JWT revocation denylist all
        read it, and it returned False for both "no key" and "Redis is
        gone"."""
        import re
        src = (cache.__file__ or "").replace(".pyc", ".py")
        text = open(src).read()
        bare = re.findall(r"\n\s+except Exception:\s*\n", text)
        assert not bare, f"{len(bare)} except block(s) still swallow silently"
