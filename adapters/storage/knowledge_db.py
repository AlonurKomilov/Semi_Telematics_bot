"""Knowledge base CRUD mixin.

System-level feature: articles live in the platform DB and are visible
across all company accounts based on visibility rules.

Visibility:
  private — only users in the same account can see the article (auto-approved)
  public  — all users across all accounts (requires admin approval first)

Ownership:
  Only the creator (created_by) can edit or delete an article.

Security:
  media_url must be https:// — javascript:, data:, file: schemes are blocked.
  Public articles require approval before cross-account visibility.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


# Valid categories for knowledge base articles
KB_CATEGORIES = {
    "maintenance":  "🔧 Maintenance & Repair",
    "fault_codes":  "⚠️ Fault Codes",
    "pre_trip":     "📋 Pre-Trip / Post-Trip",
    "compliance":   "📜 Compliance & Regulations",
    "safety":       "🛡 Driving & Safety",
    "fuel":         "⛽ Fuel Efficiency",
    "procedures":   "🏢 Company Procedures",
    "training":     "📱 Platform Training",
    "reefer":       "🧊 Reefer Operations",
    "general":      "📚 General",
}

KB_MEDIA_TYPES = {"video", "pdf", "image", "link", "none"}
KB_VISIBILITY = {"private", "public"}
KB_TARGET_ROLES = {"all", "owner", "admin", "fleet", "safety", "dispatcher", "driver"}

# Allowed URL schemes — only HTTPS
_SAFE_URL_RE = re.compile(r"^https://", re.IGNORECASE)


def validate_media_url(url: str) -> str:
    """Validate and sanitise a media URL.

    Returns the cleaned URL or raises ValueError.
    Empty string is allowed (no media).
    """
    if not url or not url.strip():
        return ""
    url = url.strip()
    if not _SAFE_URL_RE.match(url):
        raise ValueError(
            "Only https:// URLs are allowed. "
            "javascript:, data:, file: and http:// links are blocked for security."
        )
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("Invalid URL — missing hostname")
    # Block localhost / private IPs
    host = parsed.hostname.lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("URLs pointing to localhost are not allowed")
    return url


class KnowledgeBaseMixin:

    async def add_kb_article(
        self,
        account_id: int,
        title: str,
        description: str = "",
        category: str = "general",
        media_url: str = "",
        media_type: str = "link",
        tags: str = "",
        visibility: str = "private",
        target_role: str = "all",
        pinned: bool = False,
        created_by: int = 0,
        creator_name: str = "",
    ) -> int:
        now = self._now()
        # Private articles are auto-approved; public articles need admin review
        approved = 1 if visibility == "private" else 0
        cur = await self._db.execute(
            """INSERT INTO knowledge_base
               (account_id, title, description, category, media_url,
                media_type, tags, visibility, target_role, pinned,
                created_by, creator_name, approved, updated_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, title, description, category, media_url,
             media_type, tags, visibility, target_role, int(pinned),
             created_by, creator_name, approved, now, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_kb_articles(
        self,
        account_id: int,
        user_role: str = "all",
        user_id: int = 0,
        category: Optional[str] = None,
        search: Optional[str] = None,
        pinned_only: bool = False,
        include_pending: bool = False,
    ) -> list[dict]:
        """Return articles visible to this user.

        Visible means:
          • private articles from the user's own account (always approved)
          • approved public articles where target_role matches
          • any article created by this user (always visible to creator)
          • if include_pending: also show unapproved public articles from
            the user's own account (for admin review)
        """
        q = """SELECT * FROM knowledge_base WHERE (
                  (visibility = 'private' AND account_id = ?)
               OR (visibility = 'public' AND approved = 1
                   AND (target_role = 'all' OR target_role = ?))
               OR created_by = ?"""
        params: list = [account_id, user_role, user_id]
        if include_pending:
            q += " OR (visibility = 'public' AND approved = 0 AND account_id = ?)"
            params.append(account_id)
        q += ")"
        if category:
            q += " AND category = ?"
            params.append(category)
        if pinned_only:
            q += " AND pinned = 1"
        if search:
            q += " AND (title LIKE ? OR description LIKE ? OR tags LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        q += " ORDER BY pinned DESC, created_at DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_kb_pending_articles(self, account_id: int = 0) -> list[dict]:
        """Return public articles awaiting approval.

        When *account_id* is provided only that account's pending articles
        are returned (tenant-scoped). When omitted/0 returns all (platform
        super-admin use only).
        """
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM knowledge_base WHERE visibility = 'public' AND approved = 0 "
                "AND account_id = ? ORDER BY created_at DESC",
                (account_id,),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM knowledge_base WHERE visibility = 'public' AND approved = 0 "
                "ORDER BY created_at DESC",
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_kb_article(self, article_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM knowledge_base WHERE id = ?", (article_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def approve_kb_article(self, article_id: int) -> bool:
        """Approve a public article for cross-account visibility."""
        cur = await self._db.execute(
            "UPDATE knowledge_base SET approved = 1, updated_at = ? WHERE id = ?",
            (self._now(), article_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def reject_kb_article(self, article_id: int) -> bool:
        """Reject (delete) a pending public article."""
        cur = await self._db.execute(
            "DELETE FROM knowledge_base WHERE id = ? AND approved = 0",
            (article_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_kb_article(
        self, article_id: int, user_id: int, **kwargs,
    ) -> bool:
        """Update an article. Only the creator (user_id) can update.

        If visibility changes to public, reset approval to pending.
        """
        allowed = {
            "title", "description", "category", "media_url",
            "media_type", "tags", "visibility", "target_role", "pinned",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        # If switching to public, require re-approval
        if updates.get("visibility") == "public":
            updates["approved"] = 0
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [article_id, user_id]
        cur = await self._db.execute(
            f"UPDATE knowledge_base SET {set_clause} WHERE id = ? AND created_by = ?",
            values,
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_kb_article(self, article_id: int, user_id: int) -> bool:
        """Delete an article. Only the creator (user_id) can delete."""
        cur = await self._db.execute(
            "DELETE FROM knowledge_base WHERE id = ? AND created_by = ?",
            (article_id, user_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_kb_categories(
        self, account_id: int, user_role: str = "all", user_id: int = 0,
    ) -> list[dict]:
        """Get categories with article counts visible to this user."""
        cur = await self._db.execute(
            """SELECT category, COUNT(*) as count
               FROM knowledge_base
               WHERE (
                  (visibility = 'private' AND account_id = ?)
               OR (visibility = 'public' AND approved = 1
                   AND (target_role = 'all' OR target_role = ?))
               OR created_by = ?
               )
               GROUP BY category
               ORDER BY count DESC""",
            (account_id, user_role, user_id),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
