"""Smoke tests for the knowledge base CRUD surface.

Covers the pieces most likely to silently regress on a refactor:
case-insensitive search, length caps, tag normalisation, the
allow-list URL validator, and pagination math.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from adapters.storage.knowledge import (
    KB_DESCRIPTION_MAX_LEN, KB_TITLE_MAX_LEN,
    normalize_tags, validate_media_url,
)


# ── Pure helpers (no DB needed) ────────────────────────────────────


class TestTagNormalize:
    def test_empty_string_returns_empty(self):
        assert normalize_tags("") == ""
        assert normalize_tags(None or "") == ""

    def test_lowercases_and_trims(self):
        assert normalize_tags(" Freightliner , Battery ") == "freightliner, battery"

    def test_dedupes_case_insensitively(self):
        assert normalize_tags("brake, BRAKE, Brakes, brake") == "brake, brakes"

    def test_accepts_semicolon_and_whitespace_separators(self):
        assert normalize_tags("brake; brakes\ttires") == "brake, brakes, tires"

    def test_drops_one_char_tokens_and_huge_tokens(self):
        assert normalize_tags("a, brake, " + "x" * 50) == "brake"

    def test_strips_hash_underscore_dash_padding(self):
        assert normalize_tags("#brake, --tires-, _engine_") == "brake, tires, engine"


class TestValidateMediaUrl:
    def test_empty_string_is_ok(self):
        assert validate_media_url("") == ""
        assert validate_media_url("   ") == ""

    def test_http_rejected(self):
        with pytest.raises(ValueError, match="https://"):
            validate_media_url("http://youtube.com/watch?v=abc")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValueError):
            validate_media_url("javascript:alert(1)")

    def test_data_scheme_rejected(self):
        with pytest.raises(ValueError):
            validate_media_url("data:text/html,<script>alert(1)</script>")

    def test_localhost_blocked(self):
        with pytest.raises(ValueError, match="localhost"):
            validate_media_url("https://localhost/file.pdf")
        with pytest.raises(ValueError):
            validate_media_url("https://127.0.0.1/file.pdf")

    def test_arbitrary_host_blocked(self):
        with pytest.raises(ValueError, match="allowed media-host"):
            validate_media_url("https://evil.example.com/file.pdf")

    def test_youtube_allowed(self):
        assert validate_media_url("https://youtube.com/watch?v=xyz").startswith("https://")

    def test_youtube_subdomain_allowed(self):
        # The suffix-match should accept music.youtube.com under youtube.com
        out = validate_media_url("https://music.youtube.com/watch?v=xyz")
        assert "music.youtube.com" in out

    def test_drive_allowed(self):
        assert validate_media_url("https://drive.google.com/file/d/abc").startswith("https://")

    def test_url_too_long_rejected(self):
        with pytest.raises(ValueError, match="character limit"):
            validate_media_url("https://youtube.com/" + "x" * 3000)


# ── DB layer (pg_db fixture) ───────────────────────────────────────


@pytest.fixture
async def pdb(pg_db):
    """Use the same Database instance for KB ops — it carries the
    KnowledgeBaseMixin and matches the runtime platform_db shape."""
    yield pg_db


class TestStorageCrud:
    async def test_add_and_get_round_trip(self, pdb):
        acct = await pdb.create_account("Acme KB")
        article_id = await pdb.add_kb_article(
            account_id=acct.id,
            title="Front airbrake bleed",
            description="Open the bleeder…",
            category="maintenance",
            tags="brake, freightliner, AIR",
            created_by=70001,
            creator_name="Tester",
        )
        got = await pdb.get_kb_article(article_id)
        assert got is not None
        assert got["title"] == "Front airbrake bleed"
        # Tag normalization happened at insert.
        assert got["tags"] == "brake, freightliner, air"
        # Private + auto-approved.
        assert got["visibility"] == "private"
        assert int(got["approved"]) == 1

    async def test_title_and_description_are_clipped(self, pdb):
        acct = await pdb.create_account("Acme limits")
        article_id = await pdb.add_kb_article(
            account_id=acct.id,
            title="x" * (KB_TITLE_MAX_LEN + 100),
            description="y" * (KB_DESCRIPTION_MAX_LEN + 100),
            created_by=1,
        )
        got = await pdb.get_kb_article(article_id)
        assert got is not None
        assert len(got["title"]) == KB_TITLE_MAX_LEN
        assert len(got["description"]) == KB_DESCRIPTION_MAX_LEN

    async def test_case_insensitive_search(self, pdb):
        acct = await pdb.create_account("Acme search")
        await pdb.add_kb_article(
            account_id=acct.id, title="Freightliner battery check",
            created_by=1,
        )
        # Lower-case query should still match the mixed-case title.
        rows = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1,
            search="freightliner",
        )
        assert any("Freightliner" in r["title"] for r in rows)

    async def test_pagination_limit_offset(self, pdb):
        acct = await pdb.create_account("Acme paging")
        for i in range(5):
            await pdb.add_kb_article(
                account_id=acct.id, title=f"Article {i}", created_by=1,
            )
        page1 = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1,
            limit=2, offset=0,
        )
        page2 = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1,
            limit=2, offset=2,
        )
        assert len(page1) == 2
        assert len(page2) == 2
        # No overlap.
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    async def test_light_projection_omits_description(self, pdb):
        acct = await pdb.create_account("Acme light")
        await pdb.add_kb_article(
            account_id=acct.id, title="Short",
            description="this should not ship in the light projection",
            created_by=1,
        )
        full = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1, light=False,
        )
        light = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1, light=True,
        )
        assert "description" in full[0]
        assert "description" not in light[0]

    async def test_count_matches_get(self, pdb):
        acct = await pdb.create_account("Acme count")
        for i in range(7):
            await pdb.add_kb_article(
                account_id=acct.id, title=f"Article {i}", created_by=1,
            )
        total = await pdb.count_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1,
        )
        rows = await pdb.get_kb_articles(
            account_id=acct.id, user_role="owner", user_id=1, limit=500,
        )
        assert total == len(rows) == 7
