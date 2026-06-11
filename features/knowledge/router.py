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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from adapters.storage.knowledge import (
    KB_CATEGORIES, KB_DESCRIPTION_MAX_LEN, KB_TAGS_MAX_LEN,
    KB_TITLE_MAX_LEN, normalize_tags, validate_media_url,
)
from capabilities.iam.permissions import (
    is_kb_approver_role, is_kb_author_role,
)
from capabilities.knowledge.service import can_view_article as _can_view_article
from capabilities.work_orders.storage import safe_attachment_name
from interfaces.api.deps import (
    get_current_user, get_platform_db, get_tenant_db, resolve_user_id,
)

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


async def _notify_audience(
    platform_db, account_id: int, article: dict, exclude_user_id: int,
) -> int:
    """Best-effort DM every user in the article's target audience.

    Fired when an article becomes visible (private = auto-approved, or
    public = approver clicked Approve).  Returns the number of users
    successfully DM'd so the route can include it in the response —
    callers usually ignore it, but it's useful for tests.

    Skips:
      - the article author (they know they wrote it),
      - any user without a linked Telegram (telegram_id IS NULL),
      - any user whose role doesn't match ``target_role`` (when not "all").

    Failures per recipient are swallowed; one offline user shouldn't
    block the rest of the fan-out.
    """
    try:
        from infra.bot_registry import get_app_for_account
        from telegram.constants import ParseMode
    except Exception:
        return 0
    bot_app = get_app_for_account(account_id)
    if bot_app is None or not getattr(bot_app, "bot", None):
        return 0

    target_role = (article.get("target_role") or "all").strip().lower()
    try:
        users = await platform_db.list_account_users(account_id)
    except Exception as e:
        logger.debug("KB notify_audience: list_account_users failed: %s", e)
        return 0

    title = (article.get("title") or "")[:100]
    category = (article.get("category") or "general").replace("_", " ")
    visibility = (article.get("visibility") or "private")
    visibility_chip = "🌐 public" if visibility == "public" else "🔒 private"
    text = (
        f"📚 <b>New knowledge-base article</b>\n"
        f"<b>{title}</b>\n"
        f"<i>{category} · {visibility_chip}</i>"
    )

    sent = 0
    for u in users:
        try:
            if not getattr(u, "telegram_id", None):
                continue
            if int(getattr(u, "id", 0) or 0) == int(exclude_user_id or 0):
                continue
            role_value = getattr(u.role, "value", str(u.role)).lower()
            if target_role != "all" and role_value != target_role:
                continue
            await bot_app.bot.send_message(
                chat_id=u.telegram_id, text=text, parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception as e:
            logger.debug(
                "KB notify_audience: send to user_id=%s failed: %s",
                getattr(u, "id", "?"), e,
            )
    return sent


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
    internal_uid = await resolve_user_id(user)
    articles = await platform_db.get_kb_articles(
        account_id=user["account_id"],
        user_role=role,
        user_id=internal_uid,
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
        user_id=internal_uid,
        category=category,
        search=search,
        pinned_only=pinned,
        include_pending=is_kb_approver_role(role),
    )
    # Decorate each row with the requesting user's bookmark state +
    # sort bookmarked-by-me first.  One DB lookup per list response,
    # not per row.
    bookmarks: set[int] = set()
    try:
        bookmarks = await platform_db.get_kb_user_bookmark_ids(internal_uid)
    except Exception:
        pass
    decorated = []
    for a in articles:
        is_bk = int(a.get("id", 0)) in bookmarks
        decorated.append({**a, "is_bookmarked": is_bk})
    # Stable sort: bookmarked rows float to the top, the existing
    # created_at DESC ordering from the storage layer survives within
    # each group.
    decorated.sort(key=lambda r: 0 if r["is_bookmarked"] else 1)
    return {
        "articles": decorated,
        "count": len(decorated),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(decorated) < total,
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
    # Ownership comparison uses the stable internal user.id, not the
    # telegram_id — without this, email-only users wouldn't be able to
    # see their own private articles (telegram_id is NULL for them).
    internal_uid = await resolve_user_id(user)
    if not _can_view_article(
        user_id=internal_uid,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        raise HTTPException(status_code=403, detail="Not authorized to view this article")
    # Decorate the response with this user's vote so the UI can render
    # the thumbs-up / thumbs-down chip pre-selected.
    try:
        my_vote = await platform_db.get_kb_user_feedback(article_id, internal_uid)
    except Exception:
        my_vote = None
    try:
        my_bookmarks = await platform_db.get_kb_user_bookmark_ids(internal_uid)
    except Exception:
        my_bookmarks = set()
    if isinstance(article, dict):
        article = {
            **article,
            "my_vote": my_vote,
            "is_bookmarked": int(article.get("id", 0)) in my_bookmarks,
        }
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
        user_id=await resolve_user_id(user),
    )
    return {"categories": cats}


@router.post("/articles/{article_id}/view")
async def record_view(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Increment the per-article view counter.

    Dashboard pings this once per article-open (debounced client-side
    so a re-expand within ~30s doesn't double-count).  No body, no
    response payload beyond ``{"ok": true}`` — fire-and-forget.

    A 404 here just means the article was deleted between the user
    seeing it in the list and pinging — silently ignore.
    """
    article = await platform_db.get_kb_article(article_id)
    if not article:
        return {"ok": False, "reason": "not_found"}
    internal_uid = await resolve_user_id(user)
    if not _can_view_article(
        user_id=internal_uid,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        # Can't see it → can't bump its counter.  Match the 403 pattern
        # used by ``get_article`` so a confused client gets the same
        # response from either path.
        raise HTTPException(status_code=403, detail="Not authorized")
    await platform_db.record_kb_view(article_id)
    return {"ok": True}


class FeedbackRequest(BaseModel):
    helpful: bool


@router.post("/articles/{article_id}/feedback")
async def record_feedback(
    article_id: int,
    body: FeedbackRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Record (or update) the user's helpful/unhelpful vote.

    Re-voting the same way is a no-op.  Flipping a vote moves the
    counters atomically so the helpful/unhelpful totals can't drift.
    """
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    internal_uid = await resolve_user_id(user)
    if not _can_view_article(
        user_id=internal_uid,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        raise HTTPException(status_code=403, detail="Not authorized")
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")
    result = await platform_db.upsert_kb_feedback(
        article_id, internal_uid, body.helpful,
    )
    # Surface fresh counters so the UI can update in place.
    fresh = await platform_db.get_kb_article(article_id)
    return {
        "ok": True,
        "changed": result["changed"],
        "vote": result["vote"],
        "helpful_count":   int((fresh or {}).get("helpful_count", 0) or 0),
        "unhelpful_count": int((fresh or {}).get("unhelpful_count", 0) or 0),
    }


# ── Personal bookmarks ────────────────────────────────────────────


@router.post("/articles/{article_id}/bookmark")
async def add_bookmark(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Bookmark an article for the requesting user.

    Per-user — does NOT touch what other users in the same account
    see.  Idempotent: re-bookmarking is a no-op.
    """
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    internal_uid = await resolve_user_id(user)
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")
    if not _can_view_article(
        user_id=internal_uid,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        raise HTTPException(status_code=403, detail="Not authorized")
    await platform_db.add_kb_bookmark(article_id, internal_uid)
    return {"ok": True, "is_bookmarked": True}


@router.delete("/articles/{article_id}/bookmark")
async def remove_bookmark(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Remove a bookmark.  Idempotent — no error if it wasn't there."""
    internal_uid = await resolve_user_id(user)
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")
    await platform_db.remove_kb_bookmark(article_id, internal_uid)
    return {"ok": True, "is_bookmarked": False}


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
    # Rate-limit by the volatile telegram_id (one bucket per device) but
    # store ownership by the stable internal user.id so re-linking the
    # account doesn't orphan the creator's articles.
    telegram_id = int(user["sub"])
    await _check_create_rate(telegram_id)
    internal_uid = await resolve_user_id(user)
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")

    if body.media_url and not _is_internal_kb_path(body.media_url):
        try:
            body.media_url = validate_media_url(body.media_url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    body.tags = normalize_tags(body.tags)

    db_user = await platform_db.get_user_by_id(internal_uid)
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
        created_by=internal_uid,
        creator_name=creator_name,
    )
    # Private articles are auto-approved (visible immediately), so the
    # audience-notify fires here.  Public articles wait for the approval
    # flow — see ``approve_article`` for that path.
    notified = 0
    if body.visibility == "private":
        notified = await _notify_audience(
            platform_db,
            account_id=user["account_id"],
            article={
                "title": body.title, "category": body.category,
                "target_role": body.target_role, "visibility": body.visibility,
            },
            exclude_user_id=internal_uid,
        )
    return {
        "id": article_id,
        "status": "created",
        "approved": body.visibility == "private",
        "notified": notified,
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
    telegram_id = int(user["sub"])
    await _check_create_rate(telegram_id)
    internal_uid = await resolve_user_id(user)
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if int(article.get("created_by") or 0) != internal_uid:
        raise HTTPException(status_code=403, detail="Only the article creator can edit")

    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    if (
        "media_url" in kwargs and kwargs["media_url"]
        and not _is_internal_kb_path(kwargs["media_url"])
    ):
        try:
            kwargs["media_url"] = validate_media_url(kwargs["media_url"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    ok = await platform_db.update_kb_article(article_id, user_id=internal_uid, **kwargs)
    return {"ok": ok}


@router.delete("/articles/{article_id}")
async def delete_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Delete a knowledge base article. Only the creator can delete."""
    internal_uid = await resolve_user_id(user)
    if not internal_uid:
        raise HTTPException(status_code=401, detail="User not found")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if int(article.get("created_by") or 0) != internal_uid:
        raise HTTPException(status_code=403, detail="Only the article creator can delete")

    ok = await platform_db.delete_kb_article(article_id, user_id=internal_uid)
    return {"ok": ok}


# ── File upload (PDF / image) ─────────────────────────────────────
#
# Replaces the "every article needs a host on the allowlist" workflow
# for fleets that want to attach their own SOPs (Word-exported PDFs,
# photographed checklists, etc.).  Uploads go through the per-account
# object store, NOT to public URLs.
#
# Flow:
#   1. Dashboard POST /knowledge/upload (multipart/form-data)
#      → returns {file_path, file_name, file_size, content_type}.
#   2. Dashboard then POSTs /knowledge/articles with the returned
#      ``file_path`` as ``media_url`` and the inferred media_type.
#   3. Readers GET /knowledge/articles/{id}/file to stream the bytes
#      (auth-guarded by the article's normal visibility check).
#
# Storing the file_path in ``media_url`` works because the field is
# already used for the "where's this article's media live" answer.
# The validate_media_url() URL-allowlist only runs on values that look
# like ``https://`` URLs — internal paths bypass it.

_KB_UPLOAD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
_KB_UPLOAD_CONTENT_TYPES = {
    "application/pdf":          "pdf",
    "image/png":                "image",
    "image/jpeg":               "image",
    "image/webp":               "image",
}


def _is_internal_kb_path(value: str) -> bool:
    """Heuristic: did ``value`` come from our own object-store upload?

    Real URLs begin with ``https://``; internal paths start with the
    project-relative root (``data/userdata/...`` or ``account-...``).
    Used by the article-create path to decide whether to run the
    allowlist URL validator or treat the value as an opaque pointer
    into our own storage.
    """
    if not value:
        return False
    if value.startswith("http://") or value.startswith("https://"):
        return False
    return True


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Upload a PDF or image into the per-account object store.

    Returns the storage path + metadata so the dashboard can attach it
    to a knowledge-base article in a follow-up POST.  Two-step on
    purpose: the dashboard can show the uploaded file's name + size
    before the user commits to creating the article.

    Permission: any user allowed to AUTHOR articles
    (owner / admin / fleet / safety).  Drivers can't upload files for
    the same reason they can't create articles in the first place.
    """
    from adapters.storage.object_store import get_object_store_for_account

    role = user.get("role", "")
    if not is_kb_author_role(role):
        raise HTTPException(
            status_code=403,
            detail="Only owners, admins, fleet, and safety can upload files",
        )

    content_type = (file.content_type or "").lower()
    if content_type not in _KB_UPLOAD_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {content_type or 'unknown'}. "
                "Allowed: PDF, PNG, JPEG, WEBP."
            ),
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(raw) > _KB_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_KB_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit.",
        )

    safe_name = safe_attachment_name(file.filename or "attachment")
    store = await get_object_store_for_account(user["account_id"], tenant_db)
    file_path = store.put("knowledge", safe_name, raw)
    if not file_path:
        raise HTTPException(
            status_code=500, detail="Storage write failed — try again.",
        )

    media_type = _KB_UPLOAD_CONTENT_TYPES[content_type]
    return {
        "file_path":    file_path,
        "file_name":    safe_name,
        "file_size":    len(raw),
        "content_type": content_type,
        "media_type":   media_type,
    }


@router.get("/articles/{article_id}/file")
async def serve_article_file(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Stream the article's uploaded file, auth-guarded.

    The article's normal visibility rules apply — a private file can't
    leak to another account just because someone guessed the article
    id.  Falls back to 302-redirecting external URLs so the same
    endpoint can render both upload-backed and link-backed articles.
    """
    from adapters.storage.object_store import get_object_store_for_account
    from fastapi.responses import RedirectResponse

    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    internal_uid = await resolve_user_id(user)
    if not _can_view_article(
        user_id=internal_uid,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    media_url = (article.get("media_url") or "").strip()
    if not media_url:
        raise HTTPException(status_code=404, detail="Article has no file")
    # External URLs — just redirect.  The allowlist guarantees these
    # are safe-ish hosts; the browser fetches directly.
    if media_url.startswith("http://") or media_url.startswith("https://"):
        return RedirectResponse(url=media_url, status_code=302)

    # Internal upload — stream from the object store of the account
    # that OWNS this article (might differ from the requesting account
    # for an approved cross-account public article).
    owning_account = int(article.get("account_id") or 0)
    store = await get_object_store_for_account(owning_account, tenant_db)
    data: bytes | None = None
    getter = getattr(store, "get_by_id", None)
    if getter is not None:
        data = getter(media_url)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found in storage")

    content_type = "application/pdf"
    if article.get("media_type") == "image":
        # We don't store the exact MIME — sniff a tiny prefix to pick
        # between png / jpeg / webp.  Any image renderer accepts the
        # first match.
        head = data[:12]
        if head.startswith(b"\x89PNG"):
            content_type = "image/png"
        elif head[:3] == b"\xff\xd8\xff":
            content_type = "image/jpeg"
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            content_type = "image/webp"
        else:
            content_type = "application/octet-stream"

    def _iter():
        yield data

    return StreamingResponse(_iter(), media_type=content_type)


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
    notified = 0
    if ok:
        # Best-effort DM to the article creator so they know it landed.
        await _notify_creator(
            int(article.get("created_by") or 0),
            int(article.get("account_id") or 0),
            article.get("title", ""), "approved",
        )
        # Fan-out to the target audience now that the article is visible.
        notified = await _notify_audience(
            platform_db,
            account_id=int(article.get("account_id") or 0),
            article=article,
            exclude_user_id=int(article.get("created_by") or 0),
        )
    return {"ok": ok, "notified": notified, "message": "Article approved"}


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
