"""Knowledge capability — article visibility logic (SSOT).

Both the bot (interfaces/bot/knowledge.py) and the API
(interfaces/api/routes/knowledge.py) use can_view_article so
visibility rules live in exactly one place.
"""

from __future__ import annotations


def can_view_article(
    user_id: int,
    account_id: int,
    role: str,
    article: dict,
) -> bool:
    """Return True if the user may read this article.

    Visibility rules (in priority order):
    1. Creator always sees their own article.
    2. Private — same account AND role-isolated:
         - target_role='all' (legacy default) → every role in the account
         - target_role=<role> → only that role
         - Owner/Admin always see EVERY private article in their account
           (management override — needed so account admins keep
           visibility into what each team is documenting).
    3. Public + approved — visible if target_role matches (or 'all').
       From ANOTHER account it additionally needs the platform
       operator's approval and a clean quarantine flag: a public
       article reaches every tenant, so the publishing account's own
       owner cannot be the only gate.
       New public articles are always created with target_role='all'
       (the per-role selector for public was dropped); the field is
       still honoured here for backward-compat with old rows.
    4. Public + not approved — visible only to same-account management
       (review queue: Owner/Admin/Fleet/Safety).

    Kept in lockstep with ``Database.get_kb_articles`` — the SQL is the
    prefilter and this is the per-row check; drift between them shows
    up as short pages.
    """
    vis = article.get("visibility", "private")
    target = article.get("target_role", "all")
    approved = bool(article.get("approved", 1))
    same_account = article.get("account_id") == account_id

    # `created_by` defaults to 0 on the column, so an anonymous caller
    # (user_id 0) must not match every authorless row.
    if user_id and article.get("created_by") == user_id:
        return True

    if vis == "private":
        if article.get("account_id") != account_id:
            return False
        # Owner/Admin escape hatch — management sees everything in
        # their account regardless of which team authored it.
        if role in ("owner", "admin"):
            return True
        # Legacy rows + intentionally cross-team articles use 'all'.
        if target == "all":
            return True
        # Role-isolated private article — only matching role sees it.
        return target == role

    # public article
    if approved:
        if not (target == "all" or target == role):
            return False
        if same_account:
            return True
        # Someone else's article: the platform has to have blessed it,
        # and a quarantined one is withdrawn from everyone.
        if article.get("quarantined_at"):
            return False
        return bool(article.get("platform_approved"))

    # public but pending approval — only same-account management can see it
    if article.get("account_id") == account_id:
        return role in ("owner", "admin", "fleet", "safety")

    return False
