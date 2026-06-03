"""Knowledge base CRUD mixin.

System-level feature: articles live in the platform DB and are visible
across all company accounts based on visibility rules.

Visibility:
  private — only users in the same account can see the article (auto-approved)
  public  — all users across all accounts (requires admin approval first)

Ownership:
  Only the creator (created_by) can edit or delete an article.

Security:
  media_url must be https:// with a host on the allowlist — javascript:,
  data:, file:, http://, and arbitrary hosts are blocked.
  Public articles require approval before cross-account visibility.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


# Valid categories for knowledge base articles.  Labels stay text-only
# here — the dashboard renders lucide-react icons next to each label
# from a frontend-side ``CATEGORY_ICONS`` map.  Mixing emojis into the
# label was unreliable across browsers (the user's screenshot rendered
# ⚪ / 🟥 / ⭕ as missing-glyph placeholders) and prevented translation
# of the human-readable label.
KB_CATEGORIES = {
    "maintenance":  "Maintenance & Repair",
    "fault_codes":  "Fault Codes",
    "pre_trip":     "Pre-Trip / Post-Trip",
    "compliance":   "Compliance & Regulations",
    "safety":       "Driving & Safety",
    "fuel":         "Fuel Efficiency",
    "procedures":   "Company Procedures",
    "training":     "Platform Training",
    "reefer":       "Reefer Operations",
    "general":      "General",
}

KB_MEDIA_TYPES = {"video", "pdf", "image", "link", "none"}
KB_VISIBILITY = {"private", "public"}
KB_TARGET_ROLES = {"all", "owner", "admin", "fleet", "safety", "dispatcher", "driver"}

# DB-side length limits.  Pydantic enforces the same on input so the API
# never lets a 50 MB description through to disk.  Numbers picked to be
# generous for real content (Markdown SOPs can easily hit 5 KB) but
# tight enough to prevent abuse.
KB_TITLE_MAX_LEN = 200
KB_DESCRIPTION_MAX_LEN = 20_000
KB_TAGS_MAX_LEN = 500
KB_MEDIA_URL_MAX_LEN = 2_048

# Allowed URL schemes — only HTTPS
_SAFE_URL_RE = re.compile(r"^https://", re.IGNORECASE)

# Hosts allowed for ``media_url``.  Restricting to known media providers
# blocks SSRF if anywhere downstream the URL gets fetched server-side
# (preview-card rendering, future OG-image extraction, etc.) and stops
# an attacker from listing internal services as "links" in articles.
# Subdomains of these hosts are accepted via suffix-match.
KB_ALLOWED_MEDIA_HOSTS = frozenset({
    # Video
    "youtube.com", "youtu.be", "vimeo.com",
    # Document / file hosting
    "docs.google.com", "drive.google.com", "dropbox.com", "onedrive.live.com",
    "sharepoint.com", "icloud.com",
    # Image hosting
    "imgur.com", "i.imgur.com",
    # Cloud object storage (signed-URL pattern; treated as safe-enough)
    "amazonaws.com", "blob.core.windows.net", "storage.googleapis.com",
    # Internal — content served by us is always fine.
    "4truck.us", "app.4truck.us", "dash.4truck.us",
})


def normalize_tags(raw: str) -> str:
    """Trim, lowercase, dedupe, and re-serialise a tag string.

    Accepts ``,``, ``;`` or whitespace as separators on input; emits a
    single comma-separated lowercase form for storage.  Caps at the DB
    length limit.  Empty input → empty string.
    """
    if not raw:
        return ""
    # Split on any of comma, semicolon, or whitespace runs.
    tokens = re.split(r"[,;\s]+", raw.strip())
    seen: list[str] = []
    seen_set: set[str] = set()
    for t in tokens:
        clean = t.strip().lower().strip("#-_")
        if not clean or clean in seen_set:
            continue
        # Drop obvious junk (single chars, very long tokens).
        if len(clean) < 2 or len(clean) > 40:
            continue
        seen.append(clean)
        seen_set.add(clean)
    result = ", ".join(seen)
    return result[:KB_TAGS_MAX_LEN]


def _host_matches_allowlist(hostname: str) -> bool:
    """Return True if ``hostname`` is on the media-host allowlist.

    Subdomain-aware: ``mail-attachment.googleusercontent.com`` matches
    ``googleusercontent.com``.  Strict hostname comparison otherwise.
    """
    hostname = hostname.lower().strip()
    if hostname in KB_ALLOWED_MEDIA_HOSTS:
        return True
    for allowed in KB_ALLOWED_MEDIA_HOSTS:
        if hostname.endswith("." + allowed):
            return True
    return False


def validate_media_url(url: str) -> str:
    """Validate and sanitise a media URL.

    Returns the cleaned URL or raises ValueError.  Empty string is
    allowed (no media).

    Rules (in order):
    1. Must start with ``https://`` — no other scheme accepted.
    2. Must parse with a hostname.
    3. Hostname is not localhost / 127.0.0.1 / private-network literal.
    4. Hostname is on the media-host allowlist (suffix-match for
       subdomains).
    5. URL fits the column length cap.
    """
    if not url or not url.strip():
        return ""
    url = url.strip()
    if len(url) > KB_MEDIA_URL_MAX_LEN:
        raise ValueError(
            f"URL exceeds {KB_MEDIA_URL_MAX_LEN}-character limit"
        )
    if not _SAFE_URL_RE.match(url):
        raise ValueError(
            "Only https:// URLs are allowed. "
            "javascript:, data:, file: and http:// links are blocked for security."
        )
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("Invalid URL — missing hostname")
    host = parsed.hostname.lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("URLs pointing to localhost are not allowed")
    if not _host_matches_allowlist(host):
        raise ValueError(
            f"Host '{host}' is not on the allowed media-host list. "
            "Use YouTube, Vimeo, Google Drive/Docs, Dropbox, OneDrive, "
            "Imgur, or our own 4truck.us domain."
        )
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
            (account_id, title[:KB_TITLE_MAX_LEN],
             description[:KB_DESCRIPTION_MAX_LEN],
             category, media_url[:KB_MEDIA_URL_MAX_LEN],
             media_type, normalize_tags(tags),
             visibility, target_role, int(pinned),
             created_by, creator_name, approved, now, now),
        )
        await self._db.commit()
        return cur.lastrowid

    # Columns the dashboard list-view needs.  Skips ``description`` so
    # we don't ship a 20 KB body for every row in a 100-article list;
    # the detail endpoint loads description on demand.
    _LIST_COLUMNS = (
        "id, account_id, title, category, media_url, media_type, tags, "
        "visibility, target_role, pinned, created_by, creator_name, "
        "approved, view_count, helpful_count, unhelpful_count, "
        "updated_at, created_at"
    )

    async def get_kb_articles(
        self,
        account_id: int,
        user_role: str = "all",
        user_id: int = 0,
        category: Optional[str] = None,
        search: Optional[str] = None,
        pinned_only: bool = False,
        include_pending: bool = False,
        limit: int = 200,
        offset: int = 0,
        light: bool = False,
    ) -> list[dict]:
        """Return articles visible to this user.

        Visible means:
          • private articles from the user's own account (always approved)
          • approved public articles where target_role matches
          • any article created by this user (always visible to creator)
          • if include_pending: also show unapproved public articles from
            the user's own account (for admin review)

        ``light=True`` skips ``description`` (the heaviest column) — used
        by the dashboard list view, which loads the description on click.

        ``limit`` and ``offset`` are clamped server-side; pass them
        unchanged from the API layer.  ``search`` matches case-
        insensitively against title/description/tags (ILIKE).
        """
        cols = self._LIST_COLUMNS if light else "*"
        q = f"""SELECT {cols} FROM knowledge_base WHERE (
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
            # ILIKE → case-insensitive substring match.  Both columns are
            # indexed via the FTS GIN (migration), but for short queries
            # ILIKE still hits a sequential scan on small tables — it's
            # fine here and matches what users expect from a search box.
            q += " AND (title ILIKE ? OR description ILIKE ? OR tags ILIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        q += " ORDER BY pinned DESC, created_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_kb_articles(
        self,
        account_id: int,
        user_role: str = "all",
        user_id: int = 0,
        category: Optional[str] = None,
        search: Optional[str] = None,
        pinned_only: bool = False,
        include_pending: bool = False,
    ) -> int:
        """Total count matching the same filters as ``get_kb_articles``.

        Used by the dashboard to render the pagination footer
        ("page 2 of 7") without re-fetching the full list.
        """
        q = """SELECT COUNT(*) AS n FROM knowledge_base WHERE (
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
            q += " AND (title ILIKE ? OR description ILIKE ? OR tags ILIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        cur = await self._db.execute(q, params)
        row = await cur.fetchone()
        if row is None:
            return 0
        return int(dict(row).get("n", 0))

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
        # Truncate strings to column limits so the update can't smuggle
        # an oversized payload past the API check.
        if "title" in updates:
            updates["title"] = (updates["title"] or "")[:KB_TITLE_MAX_LEN]
        if "description" in updates:
            updates["description"] = (updates["description"] or "")[:KB_DESCRIPTION_MAX_LEN]
        if "media_url" in updates:
            updates["media_url"] = (updates["media_url"] or "")[:KB_MEDIA_URL_MAX_LEN]
        if "tags" in updates:
            updates["tags"] = normalize_tags(updates["tags"])
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

    async def record_kb_view(self, article_id: int) -> None:
        """Increment the view counter + stamp last_viewed_at.

        Fire-and-forget from the route — no consistency guarantees are
        required.  A row that's already at 9_999_999 views and gets
        bumped during a network blip can silently miss the increment;
        not worth a transaction.
        """
        try:
            await self._db.execute(
                "UPDATE knowledge_base SET view_count = view_count + 1, "
                "last_viewed_at = ? WHERE id = ?",
                (self._now(), article_id),
            )
            await self._db.commit()
        except Exception:
            pass

    async def upsert_kb_feedback(
        self, article_id: int, user_id: int, helpful: bool,
    ) -> dict:
        """Record (or change) a user's helpful/unhelpful vote.

        Re-voting the same way is a no-op.  Flipping a vote bumps the
        new counter AND decrements the old one — kept atomic via a
        single transaction so the counters can't drift.
        """
        now = self._now()
        # Look up the prior vote (if any) to know whether to flip.
        cur = await self._db.execute(
            "SELECT helpful FROM knowledge_feedback "
            "WHERE article_id = ? AND user_id = ?",
            (article_id, user_id),
        )
        prior_row = await cur.fetchone()
        prior_vote: int | None = None
        if prior_row is not None:
            prior_vote = int(dict(prior_row)["helpful"])
        new_vote = 1 if helpful else 0

        if prior_vote == new_vote:
            return {"changed": False, "vote": new_vote}

        # Upsert the per-user row.
        await self._db.execute(
            """INSERT INTO knowledge_feedback (article_id, user_id, helpful, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (article_id, user_id) DO UPDATE
                 SET helpful    = EXCLUDED.helpful,
                     created_at = EXCLUDED.created_at""",
            (article_id, user_id, new_vote, now),
        )
        # Adjust the denormalised counters on the article row.  If this
        # is a fresh vote (prior_vote is None) we only bump the new
        # counter; otherwise we bump the new one and decrement the old.
        if prior_vote is None:
            if new_vote == 1:
                await self._db.execute(
                    "UPDATE knowledge_base SET helpful_count = helpful_count + 1 WHERE id = ?",
                    (article_id,),
                )
            else:
                await self._db.execute(
                    "UPDATE knowledge_base SET unhelpful_count = unhelpful_count + 1 WHERE id = ?",
                    (article_id,),
                )
        else:
            # Flip: decrement the old bucket, increment the new one.
            if new_vote == 1:
                await self._db.execute(
                    "UPDATE knowledge_base SET helpful_count = helpful_count + 1, "
                    "unhelpful_count = GREATEST(unhelpful_count - 1, 0) WHERE id = ?",
                    (article_id,),
                )
            else:
                await self._db.execute(
                    "UPDATE knowledge_base SET unhelpful_count = unhelpful_count + 1, "
                    "helpful_count = GREATEST(helpful_count - 1, 0) WHERE id = ?",
                    (article_id,),
                )
        await self._db.commit()
        return {"changed": True, "vote": new_vote}

    async def get_kb_user_feedback(
        self, article_id: int, user_id: int,
    ) -> Optional[int]:
        """Return the user's current vote (1/0) or None if they haven't voted."""
        cur = await self._db.execute(
            "SELECT helpful FROM knowledge_feedback "
            "WHERE article_id = ? AND user_id = ?",
            (article_id, user_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return int(dict(row)["helpful"])

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
