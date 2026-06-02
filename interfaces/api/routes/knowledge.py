"""Knowledge Base API endpoints — system-level CRUD for tips & guides.

Articles are stored in the platform DB (shared across all accounts).
  • Private articles are visible only within the creator's account.
  • Public articles are visible to all accounts (optionally filtered by role).
  • Public articles require owner/admin approval before cross-account visibility.
  • Only the creator can edit or delete their own articles.
  • Media URLs are validated (HTTPS only, no private IPs/localhost,
    must hit the media-host allowlist).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from adapters.storage.knowledge import (
    KB_CATEGORIES, KB_DESCRIPTION_MAX_LEN, KB_TAGS_MAX_LEN,
    KB_TITLE_MAX_LEN, normalize_tags, validate_media_url,
)
from capabilities.iam.permissions import (
    is_kb_approver_role, is_kb_author_role,
)
from capabilities.knowledge.service import can_view_article as _can_view_article
from interfaces.api.deps import get_current_user, get_platform_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ── Rate limiting (per-user, in-memory) ─────────────────────────────
#
# Token-bucket-ish counter keyed by telegram_id.  A compromised admin
# token spamming /articles maxes out at ``_KB_CREATE_RATE`` requests
# per ``_KB_CREATE_WINDOW`` seconds.  In-memory is fine for a single
# API process; for a multi-worker deploy this would move to Redis.
_KB_CREATE_RATE = 10              # max creates/edits per window
_KB_CREATE_WINDOW = 60            # window in seconds
_create_log: dict[int, deque[float]] = defaultdict(deque)
_create_log_lock = asyncio.Lock()


async def _check_create_rate(user_id: int) -> None:
    """Raise 429 if the user has exceeded the create/edit rate.

    Per-user, in-memory token bucket — deque of recent timestamps.
    Each call evicts stale entries and rejects when the bucket is full.
    """
    now = time.monotonic()
    async with _create_log_lock:
        bucket = _create_log[user_id]
        cutoff = now - _KB_CREATE_WINDOW
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _KB_CREATE_RATE:
            retry_in = int(bucket[0] + _KB_CREATE_WINDOW - now) + 1
            raise HTTPException(
                status_code=429,
                detail={
                    "message": (
                        f"Too many knowledge-base writes — try again in "
                        f"{retry_in} second(s)."
                    ),
                    "error_code": "kb_rate_limited",
                    "retry_after": retry_in,
                },
                headers={"Retry-After": str(retry_in)},
            )
        bucket.append(now)


# ── Notifications (best-effort Telegram DM to article creator) ───────


async def _notify_creator(
    creator_telegram_id: int, account_id: int,
    title: str, outcome: str,
) -> None:
    """DM the article creator with the approval outcome.

    Failures are swallowed and logged — the route's primary action
    (approve / reject) has already committed by the time we get here.
    """
    if not creator_telegram_id:
        return
    try:
        from infra.bot_registry import get_app_for_account
        from telegram.constants import ParseMode
    except Exception:
        # Bot deps not available (API-only worker) — skip notify.
        return
    bot_app = get_app_for_account(account_id)
    if bot_app is None or not getattr(bot_app, "bot", None):
        return

    emoji = "✅" if outcome == "approved" else "🚫"
    verb = "approved" if outcome == "approved" else "rejected and removed"
    text = (
        f"{emoji} Your knowledge-base article "
        f"<b>{title[:100]}</b> was {verb} by your account admin."
    )
    try:
        await bot_app.bot.send_message(
            chat_id=creator_telegram_id, text=text, parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.debug(
            "KB %s notify to %d failed: %s",
            outcome, creator_telegram_id, e,
        )


# ── Pydantic models ─────────────────────────────────────────────────


_CATEGORY_PATTERN = (
    r"^(maintenance|fault_codes|pre_trip|compliance|safety|fuel|"
    r"procedures|training|reefer|general)$"
)
_MEDIA_TYPE_PATTERN = r"^(video|pdf|image|link|none)$"
_VISIBILITY_PATTERN = r"^(private|public)$"
_TARGET_ROLE_PATTERN = (
    r"^(all|owner|admin|fleet|safety|dispatcher|driver)$"
)


class ArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=KB_TITLE_MAX_LEN)
    description: str = Field("", max_length=KB_DESCRIPTION_MAX_LEN)
    category: str = Field("general", pattern=_CATEGORY_PATTERN)
    media_url: str = Field("", max_length=2_048)
    media_type: str = Field("link", pattern=_MEDIA_TYPE_PATTERN)
    tags: str = Field("", max_length=KB_TAGS_MAX_LEN)
    visibility: str = Field("private", pattern=_VISIBILITY_PATTERN)
    target_role: str = Field("all", pattern=_TARGET_ROLE_PATTERN)
    pinned: bool = False


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=KB_TITLE_MAX_LEN)
    description: Optional[str] = Field(None, max_length=KB_DESCRIPTION_MAX_LEN)
    category: Optional[str] = Field(None, pattern=_CATEGORY_PATTERN)
    media_url: Optional[str] = Field(None, max_length=2_048)
    media_type: Optional[str] = Field(None, pattern=_MEDIA_TYPE_PATTERN)
    tags: Optional[str] = Field(None, max_length=KB_TAGS_MAX_LEN)
    visibility: Optional[str] = Field(None, pattern=_VISIBILITY_PATTERN)
    target_role: Optional[str] = Field(None, pattern=_TARGET_ROLE_PATTERN)
    pinned: Optional[bool] = None


# ── Read endpoints ──────────────────────────────────────────────────


@router.get("/permissions")
async def my_permissions(user: dict = Depends(get_current_user)):
    """Return what THIS user can do in the KB.

    Single source of truth for the frontend — keeps the UI from
    rendering buttons the backend will 403.
    """
    role = user.get("role", "")
    return {
        "can_create": is_kb_author_role(role),
        "can_approve": is_kb_approver_role(role),
    }


@router.get("/categories")
async def list_categories(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return all available categories with labels."""
    return {
        "categories": [
            {"key": k, "label": v} for k, v in KB_CATEGORIES.items()
        ]
    }


@router.get("/articles")
async def list_articles(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    pinned: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """List knowledge base articles visible to the current user.

    Returns a lightweight projection (no description body) so the list
    view stays fast even at thousands of articles.  Use
    ``GET /articles/{id}`` for the full body.
    """
    role = user.get("role", "driver")
    articles = await platform_db.get_kb_articles(
        account_id=user["account_id"],
        user_role=role,
        user_id=int(user["sub"]),
        category=category,
        search=search,
        pinned_only=pinned,
        include_pending=is_kb_approver_role(role),
        limit=limit,
        offset=offset,
        light=True,
    )
    total = await platform_db.count_kb_articles(
        account_id=user["account_id"],
        user_role=role,
        user_id=int(user["sub"]),
        category=category,
        search=search,
        pinned_only=pinned,
        include_pending=is_kb_approver_role(role),
    )
    return {
        "articles": articles,
        "count": len(articles),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(articles) < total,
    }


@router.get("/articles/{article_id}")
async def get_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Get a single knowledge base article."""
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    user_id = int(user["sub"])
    if not _can_view_article(
        user_id=user_id,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        raise HTTPException(status_code=403, detail="Not authorized to view this article")
    return article


@router.get("/stats")
async def article_stats(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Get category counts for visible articles."""
    cats = await platform_db.get_kb_categories(
        account_id=user["account_id"],
        user_role=user.get("role", "driver"),
        user_id=int(user["sub"]),
    )
    return {"categories": cats}


# ── Write endpoints ─────────────────────────────────────────────────


@router.post("/articles")
async def create_article(
    body: ArticleCreate,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Create a new knowledge base article."""
    role = user.get("role", "")
    if not is_kb_author_role(role):
        raise HTTPException(
            status_code=403,
            detail="Only owners, admins, fleet, and safety can create articles",
        )
    user_id = int(user["sub"])
    await _check_create_rate(user_id)

    if body.media_url:
        try:
            body.media_url = validate_media_url(body.media_url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    body.tags = normalize_tags(body.tags)

    db_user = await platform_db.get_user_by_telegram_id(user_id)
    creator_name = db_user.display_name if db_user else ""

    article_id = await platform_db.add_kb_article(
        account_id=user["account_id"],
        title=body.title,
        description=body.description,
        category=body.category,
        media_url=body.media_url,
        media_type=body.media_type,
        tags=body.tags,
        visibility=body.visibility,
        target_role=body.target_role,
        pinned=body.pinned,
        created_by=user_id,
        creator_name=creator_name,
    )
    return {
        "id": article_id,
        "status": "created",
        "approved": body.visibility == "private",
        "message": (
            "Public article submitted for admin approval"
            if body.visibility == "public"
            else "Article created"
        ),
    }


@router.put("/articles/{article_id}")
async def update_article(
    article_id: int,
    body: ArticleUpdate,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Update a knowledge base article. Only the creator can update."""
    user_id = int(user["sub"])
    await _check_create_rate(user_id)
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Only the article creator can edit")

    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    if "media_url" in kwargs and kwargs["media_url"]:
        try:
            kwargs["media_url"] = validate_media_url(kwargs["media_url"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    ok = await platform_db.update_kb_article(article_id, user_id=user_id, **kwargs)
    return {"ok": ok}


@router.delete("/articles/{article_id}")
async def delete_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Delete a knowledge base article. Only the creator can delete."""
    user_id = int(user["sub"])
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Only the article creator can delete")

    ok = await platform_db.delete_kb_article(article_id, user_id=user_id)
    return {"ok": ok}


# ── Approval workflow ─────────────────────────────────────────────


@router.get("/pending")
async def list_pending_articles(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """List public articles pending approval (owner/admin only)."""
    if not is_kb_approver_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners and admins can review pending articles")
    articles = await platform_db.get_kb_pending_articles(account_id=user["account_id"])
    return {"articles": articles, "count": len(articles)}


@router.post("/articles/{article_id}/approve")
async def approve_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Approve a public article for cross-account visibility (owner/admin only)."""
    if not is_kb_approver_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners and admins can approve articles")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("account_id") != user["account_id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("approved"):
        return {"ok": True, "message": "Already approved"}
    ok = await platform_db.approve_kb_article(article_id)
    if ok:
        # Best-effort DM to the article creator so they know it landed.
        await _notify_creator(
            int(article.get("created_by") or 0),
            int(article.get("account_id") or 0),
            article.get("title", ""), "approved",
        )
    return {"ok": ok, "message": "Article approved"}


@router.post("/articles/{article_id}/reject")
async def reject_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Reject and delete a pending public article (owner/admin only)."""
    if not is_kb_approver_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners and admins can reject articles")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("account_id") != user["account_id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    # Snapshot creator + title BEFORE the delete since reject_kb_article
    # removes the row.  Lets us still DM the creator afterwards.
    creator_id = int(article.get("created_by") or 0)
    creator_account = int(article.get("account_id") or 0)
    title = article.get("title", "")
    ok = await platform_db.reject_kb_article(article_id)
    if ok:
        await _notify_creator(creator_id, creator_account, title, "rejected")
    return {"ok": ok, "message": "Article rejected and removed"}
