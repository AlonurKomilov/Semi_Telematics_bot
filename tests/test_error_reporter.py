"""Tests for infra.error_reporter — built-in error capture."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import infra.error_reporter as reporter


# ── Helpers ──────────────────────────────────────────────────────

def _reset():
    """Reset reporter module-level state between tests."""
    reporter._bot_token = ""
    reporter._chat_id = ""
    reporter._db_ref = None
    reporter._cooldown.clear()


def _make_db():
    db = MagicMock()
    db.log_error = AsyncMock()
    return db


# ── Tests ─────────────────────────────────────────────────────────

class TestReportError:

    @pytest.fixture(autouse=True)
    def reset_state(self):
        _reset()
        yield
        _reset()

    async def test_sends_telegram_on_first_error(self):
        """report_error() sends one Telegram message when chat_id is set."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        exc = ValueError("boom")
        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await reporter.report_error(exc, source="scheduler", job_name="fault_check", account_id=7)

        mock_post.assert_awaited_once()
        call_kwargs = mock_post.call_args.kwargs["json"]
        assert call_kwargs["chat_id"] == "-100123"
        assert "ValueError" in call_kwargs["text"]
        assert "boom" in call_kwargs["text"]

    async def test_rate_limits_telegram_but_still_writes_db(self):
        """Same error twice within cooldown: 1 Telegram call, 2 DB writes."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        # Force in-memory cooldown (no Redis in tests)
        exc = RuntimeError("dup error")

        post_calls = []

        async def fake_post(*args, **kwargs):
            post_calls.append(kwargs)
            return MagicMock(status_code=200)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(side_effect=fake_post))
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Patch redis exists to simulate key already set after first call
            call_count = 0

            async def fake_exists(key):
                nonlocal call_count
                call_count += 1
                return call_count > 1   # second call returns True (key exists)

            async def fake_setex(key, ttl):
                pass

            with patch("infra.cache.exists", fake_exists), \
                 patch("infra.cache.setex_flag", fake_setex):

                await reporter.report_error(exc, source="scheduler")
                await reporter.report_error(exc, source="scheduler")

        # Only 1 Telegram, but 2 DB writes
        assert len(post_calls) == 1
        assert db.log_error.await_count == 2

    async def test_does_not_raise_on_bad_input(self):
        """report_error() must never raise, even with None exc."""
        _reset()
        # No init_error_reporter called — everything is empty
        await reporter.report_error(None, source="unknown")   # should not raise

    async def test_no_telegram_when_chat_id_empty(self):
        """No Telegram call if ERROR_REPORT_CHAT_ID is not configured."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "", db)   # empty chat_id

        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await reporter.report_error(ValueError("x"), source="api")

        mock_post.assert_not_awaited()

    async def test_no_crash_when_db_is_none(self):
        """reporter is safe when DB is not yet ready (startup errors)."""
        reporter.init_error_reporter("fake:token", "-100123", None)

        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise even with no DB
            await reporter.report_error(RuntimeError("startup"), source="startup")

    async def test_rate_key_separate_per_account(self):
        """Same error type from different accounts → each gets its own Telegram."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        exc1 = ValueError("bad value")
        exc2 = ValueError("bad value")
        post_calls = []

        async def fake_post(*args, **kwargs):
            post_calls.append(kwargs)
            return MagicMock(status_code=200)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(side_effect=fake_post))
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            # Two different accounts — both should get a Telegram notification
            await reporter.report_error(exc1, source="scheduler", account_id=3)
            await reporter.report_error(exc2, source="scheduler", account_id=9)

        assert len(post_calls) == 2, "Each account should get its own notification"

    async def test_api_job_name_contains_path(self):
        """API errors should record HTTP method+path in the Telegram message."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        exc = RuntimeError("db error")
        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await reporter.report_error(exc, source="api", job_name="GET /api/fleet")

        call_kwargs = mock_post.call_args.kwargs["json"]
        assert "GET /api/fleet" in call_kwargs["text"]

    async def test_traceback_html_chars_escaped(self):
        """Traceback containing < > & must be HTML-escaped in <pre> block."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        # Craft an exception whose message and traceback contain HTML chars
        # Store outside the except-block scope so Python doesn't clear it
        _captured_exc = None
        try:
            raise ValueError("value < 0 & result > max")
        except ValueError as e:
            _captured_exc = e

        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await reporter.report_error(_captured_exc, source="api")

        text = mock_post.call_args.kwargs["json"]["text"]
        # Raw < > & must NOT appear inside the <pre> block
        pre_start = text.index("<pre>") + 5
        pre_end = text.index("</pre>")
        pre_content = text[pre_start:pre_end]
        assert "<" not in pre_content.replace("&lt;", "").replace("&gt;", "")
        assert "&lt;" in pre_content or "&gt;" in pre_content  # escaping applied

    async def test_error_msg_html_escaped_in_code_block(self):
        """error_msg with HTML chars must be escaped — prevents silent Telegram 400."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        _exc = None
        try:
            raise ValueError("expected <int> got <str> with a&b")
        except ValueError as e:
            _exc = e

        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await reporter.report_error(_exc, source="api")

        text = mock_post.call_args.kwargs["json"]["text"]
        # The raw unescaped chars must not appear outside of allowed HTML tags
        # They should be escaped as &lt; &gt; &amp;
        assert "<int>" not in text
        assert "&lt;int&gt;" in text
        assert "a&b" not in text
        assert "a&amp;b" in text

    async def test_job_name_html_escaped_in_header(self):
        """job_name with & (e.g. query string) must be escaped in header."""
        db = _make_db()
        reporter.init_error_reporter("fake:token", "-100123", db)

        with patch("httpx.AsyncClient") as MockClient:
            mock_post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=mock_post)
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await reporter.report_error(
                RuntimeError("fail"),
                source="api",
                job_name="GET /api/fleet?page=1&limit=50",
            )

        text = mock_post.call_args.kwargs["json"]["text"]
        # Raw & in query string must be escaped
        assert "page=1&limit=50" not in text
        assert "page=1&amp;limit=50" in text
