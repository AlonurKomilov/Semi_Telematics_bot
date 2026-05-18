"""Knowledge Base API endpoints — system-level CRUD for tips & guides.

Articles are stored in the platform DB (shared across all accounts).
  • Private articles are visible only within the creator's account.
  • Public articles are visible to all accounts (optionally filtered by role).
  • Public articles require owner/admin approval before cross-account visibility.
  • Only the creator can edit or delete their own articles.
  • Media URLs are validated (HTTPS only, no private IPs/localhost).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import get_current_user, get_platform_db
from adapters.storage.knowledge import (
    KB_CATEGORIES, validate_media_url,
)
from capabilities.iam.permissions import is_management_role
from capabilities.knowledge.service import can_view_article as _can_view_article

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class ArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    category: str = Field("general", pattern=r"^(maintenance|fault_codes|pre_trip|compliance|safety|fuel|procedures|training|reefer|general)$")
    media_url: str = ""
    media_type: str = Field("link", pattern=r"^(video|pdf|image|link|none)$")
    tags: str = ""
    visibility: str = Field("private", pattern=r"^(private|public)$")
    target_role: str = Field("all", pattern=r"^(all|owner|admin|fleet|safety|dispatcher|driver)$")
    pinned: bool = False


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = Field(None, pattern=r"^(maintenance|fault_codes|pre_trip|compliance|safety|fuel|procedures|training|reefer|general)$")
    media_url: Optional[str] = None
    media_type: Optional[str] = Field(None, pattern=r"^(video|pdf|image|link|none)$")
    tags: Optional[str] = None
    visibility: Optional[str] = Field(None, pattern=r"^(private|public)$")
    target_role: Optional[str] = Field(None, pattern=r"^(all|owner|admin|fleet|safety|dispatcher|driver)$")
    pinned: Optional[bool] = None


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
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """List knowledge base articles visible to the current user."""
    role = user.get("role", "driver")
    articles = await platform_db.get_kb_articles(
        account_id=user["account_id"],
        user_role=role,
        user_id=int(user["sub"]),
        category=category,
        search=search,
        pinned_only=pinned,
        include_pending=is_management_role(role),
    )
    return {"articles": articles, "count": len(articles)}


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
    # Check visibility using shared capability
    user_id = int(user["sub"])
    is_visible = _can_view_article(
        user_id=user_id,
        account_id=user["account_id"],
        role=user.get("role", "driver"),
        article=article,
    )
    if not is_visible:
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


@router.post("/articles")
async def create_article(
    body: ArticleCreate,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Create a new knowledge base article."""
    if not is_management_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners, admins, fleet, and safety can create articles")

    # Validate media URL security
    if body.media_url:
        try:
            body.media_url = validate_media_url(body.media_url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # Look up creator display name
    user_id = int(user["sub"])
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
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Only the article creator can edit")

    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    # Validate media URL security
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
    if not is_management_role(user.get("role", "")):
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
    if not is_management_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners and admins can approve articles")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("account_id") != user["account_id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("approved"):
        return {"ok": True, "message": "Already approved"}
    ok = await platform_db.approve_kb_article(article_id)
    return {"ok": ok, "message": "Article approved"}


@router.post("/articles/{article_id}/reject")
async def reject_article(
    article_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Reject and delete a pending public article (owner/admin only)."""
    if not is_management_role(user.get("role", "")):
        raise HTTPException(status_code=403, detail="Only owners and admins can reject articles")
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.get("account_id") != user["account_id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    ok = await platform_db.reject_kb_article(article_id)
    return {"ok": ok, "message": "Article rejected and removed"}
