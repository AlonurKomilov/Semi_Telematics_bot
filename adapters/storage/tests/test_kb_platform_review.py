"""Publishing into every OTHER account takes two approvals.

A public knowledge-base article is readable by every account on the
platform and is fed to every account's AI assistant.  The approval used
to come from the PUBLISHING account's own owner or admin — and with open
signup, that is anyone who can complete a registration form.  These
tests pin the second gate, the quarantine withdrawal, and the
authorless-row hole the assistant could fall through.

Two accounts throughout: A publishes, B reads.  A one-account test
cannot see any of this.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest


@pytest.fixture
async def pdb(pg_db):
    yield pg_db


async def _two_accounts(pdb):
    a = await pdb.create_account("Publisher Co")
    b = await pdb.create_account("Reader Co")
    return a.id, b.id


async def _public_article(pdb, account_id: int, *, title: str, author: int = 7001):
    """An article submitted as public — created UNAPPROVED, as the
    product does (`approved = 1 if visibility == 'private' else 0`)."""
    return await pdb.add_kb_article(
        account_id=account_id, title=title, description="body text",
        category="maintenance", visibility="public",
        created_by=author, creator_name="Author",
    )


def _titles(rows):
    return {r["title"] for r in rows}


class TestCrossAccountPublishingNeedsThePlatform:
    async def test_account_approval_alone_does_not_reach_another_account(self, pdb):
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Self approved SOP")

        # The publishing account's own owner approves — the only gate
        # that used to exist.
        assert await pdb.approve_kb_article(art)

        # Account A sees it.  Account B must not, until the platform says so.
        mine = await pdb.get_kb_articles(account_id=a, user_role="owner", user_id=7001)
        assert "Self approved SOP" in _titles(mine)

        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Self approved SOP" not in _titles(theirs), (
            "one account's owner approved their own article into another "
            "account's knowledge base — and from there into its assistant"
        )

    async def test_platform_approval_publishes_it(self, pdb):
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Reviewed SOP")
        await pdb.approve_kb_article(art)
        assert await pdb.platform_approve_kb_article(art, note="checked")

        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Reviewed SOP" in _titles(theirs)

    async def test_platform_cannot_publish_what_the_account_has_not_approved(self, pdb):
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Not yet submitted")
        # No account approval yet — the operator's UPDATE must not match.
        assert not await pdb.platform_approve_kb_article(art)
        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Not yet submitted" not in _titles(theirs)

    async def test_the_count_agrees_with_the_rows(self, pdb):
        """Pagination breaks when the count and the list use different
        gates — the two queries are copies of each other by hand."""
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Counted SOP")
        await pdb.approve_kb_article(art)

        for stage in ("before", "after"):
            if stage == "after":
                await pdb.platform_approve_kb_article(art)
            rows = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
            n = await pdb.count_kb_articles(account_id=b, user_role="owner", user_id=9002)
            assert n == len(rows), f"count/list drift {stage} platform approval"

    async def test_pending_queue_lists_it_for_the_operator(self, pdb):
        a, _b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Waiting SOP")
        assert not any(r["id"] == art for r in await pdb.list_kb_platform_pending())
        await pdb.approve_kb_article(art)
        assert any(r["id"] == art for r in await pdb.list_kb_platform_pending())
        await pdb.platform_approve_kb_article(art)
        assert not any(r["id"] == art for r in await pdb.list_kb_platform_pending())
        assert any(r["id"] == art for r in await pdb.list_kb_published_platform_wide())


class TestUnpublish:
    async def test_unpublish_withdraws_it_and_keeps_the_row(self, pdb):
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Withdrawn SOP")
        await pdb.approve_kb_article(art)
        await pdb.platform_approve_kb_article(art)

        assert await pdb.platform_unpublish_kb_article(art, note="off-topic")

        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Withdrawn SOP" not in _titles(theirs)

        # Refusing publication is not deleting somebody's work: the row
        # survives as the authoring account's own private article.
        row = await pdb.get_kb_article(art)
        assert row is not None
        assert row["visibility"] == "private"
        assert row["platform_review_note"] == "off-topic"
        mine = await pdb.get_kb_articles(account_id=a, user_role="owner", user_id=7001)
        assert "Withdrawn SOP" in _titles(mine)


class TestQuarantineWithdrawsThePublication:
    async def test_quarantine_removes_it_from_every_other_account(self, pdb):
        """Quarantine used to set a flag that only the file-download
        route read, so the TEXT stayed in every account's list and in
        the assistant's search."""
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Infected SOP")
        await pdb.approve_kb_article(art)
        await pdb.platform_approve_kb_article(art)
        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Infected SOP" in _titles(theirs)          # published

        await pdb.mark_article_quarantined(art, reason="EICAR-Test-Signature")

        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Infected SOP" not in _titles(theirs)

        row = await pdb.get_kb_article(art)
        assert row["visibility"] == "private"
        assert int(row["approved"]) == 0
        assert int(row["platform_approved"]) == 0

    async def test_restore_does_not_silently_republish(self, pdb):
        """The operator is clearing a virus flag, not re-blessing the
        content — going out again means both approvals from the start."""
        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Cleaned SOP")
        await pdb.approve_kb_article(art)
        await pdb.platform_approve_kb_article(art)
        await pdb.mark_article_quarantined(art, reason="false positive")
        await pdb.restore_quarantined_article(art)

        theirs = await pdb.get_kb_articles(account_id=b, user_role="owner", user_id=9002)
        assert "Cleaned SOP" not in _titles(theirs)


class TestAuthorlessRows:
    async def test_a_caller_with_no_identity_does_not_inherit_every_row(self, pdb):
        """`created_by` defaults to 0 on the column, and the AI tool
        passes no caller identity, so `created_by = 0` matched every
        authorless row regardless of visibility or approval."""
        a, b = await _two_accounts(pdb)
        art = await pdb.add_kb_article(
            account_id=a, title="Authorless private note",
            description="body", visibility="private",
            created_by=0, creator_name="",
        )
        assert art

        anonymous = await pdb.get_kb_articles(account_id=b, user_role="all", user_id=0)
        assert "Authorless private note" not in _titles(anonymous)
        n = await pdb.count_kb_articles(account_id=b, user_role="all", user_id=0)
        assert n == len(anonymous)


class TestTheInMemoryMirrorAgrees:
    """`service.can_view_article` is the per-row check behind the SQL
    prefilter.  Drift between them shows up as short pages."""

    async def test_mirror_matches_sql_at_every_stage(self, pdb):
        from features.knowledge.service import can_view_article

        a, b = await _two_accounts(pdb)
        art = await _public_article(pdb, a, title="Mirrored SOP")

        async def agree(stage: str):
            row = await pdb.get_kb_article(art)
            rows = await pdb.get_kb_articles(
                account_id=b, user_role="owner", user_id=9002)
            sql_says = "Mirrored SOP" in _titles(rows)
            mirror_says = can_view_article(9002, b, "owner", row)
            assert sql_says == mirror_says, (
                f"SQL and can_view_article disagree at {stage}: "
                f"sql={sql_says} mirror={mirror_says}"
            )
            return sql_says

        assert await agree("submitted") is False
        await pdb.approve_kb_article(art)
        assert await agree("account-approved") is False
        await pdb.platform_approve_kb_article(art)
        assert await agree("platform-approved") is True
        await pdb.mark_article_quarantined(art, reason="x")
        assert await agree("quarantined") is False
