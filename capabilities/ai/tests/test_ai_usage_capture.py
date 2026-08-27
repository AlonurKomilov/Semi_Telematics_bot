"""The AI client's usage capture — what a generation records.

Split from tests/test_new_features.py — 139 tests, 23 classes, whose
docstring listed seven unrelated subjects and then grew four more on
top. "New features" named WHEN they arrived, not what they are, which
is how one file came to hold four owners.
"""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage import Database, Role, User


@pytest_asyncio.fixture
async def seeded(db: Database):
    account = await db.create_account("Test Fleet")
    owner = await db.create_user(telegram_id=100001, account_id=account.id, role=Role.OWNER)
    driver = await db.create_user(telegram_id=100002, account_id=account.id, role=Role.DRIVER, truck_num="101")
    return {"db": db, "account": account, "owner": owner, "driver": driver}


class TestAIClientUsageCapture:
    """Tests for ai._capture_usage — now a PURE function.

    The old ``get_last_usage()`` module-global accessor was removed
    because it raced across concurrent users (one task's usage could
    land in another task's audit row).  Usage now travels with the
    call's return value; ``_capture_usage`` extracts and returns a
    dict (or None) with no side effects.
    """

    def test_capture_usage_with_metadata(self):
        import capabilities.ai as ai

        class FakeMeta:
            prompt_token_count = 120
            candidates_token_count = 80
            total_token_count = 200

        class FakeResponse:
            usage_metadata = FakeMeta()

        usage = ai._capture_usage(FakeResponse())
        assert usage is not None
        assert usage["prompt_tokens"] == 120
        assert usage["reply_tokens"] == 80
        assert usage["total_tokens"] == 200

    def test_capture_usage_no_metadata(self):
        import capabilities.ai as ai

        class FakeResponse:
            pass

        assert ai._capture_usage(FakeResponse()) is None

    def test_capture_usage_exception_safe(self):
        import capabilities.ai as ai

        class BadResponse:
            @property
            def usage_metadata(self):
                raise RuntimeError("boom")

        assert ai._capture_usage(BadResponse()) is None

    def test_capture_usage_no_global_leak(self):
        """Sanity check: ``_capture_usage`` must NOT mutate any module
        global — back-to-back calls with different responses must each
        return only their own usage, never a stale value from a prior
        call.  This was the bug that motivated the refactor.
        """
        import capabilities.ai as ai

        class FakeMeta:
            prompt_token_count = 10
            candidates_token_count = 5
            total_token_count = 15

        class FakeResponse:
            usage_metadata = FakeMeta()

        first = ai._capture_usage(FakeResponse())
        assert first is not None

        class EmptyResponse:
            pass

        second = ai._capture_usage(EmptyResponse())
        # The empty response must independently return None, regardless
        # of what the previous call returned.
        assert second is None
        # And the global was never set, so it isn't accessible.
        assert not hasattr(ai, "get_last_usage")
