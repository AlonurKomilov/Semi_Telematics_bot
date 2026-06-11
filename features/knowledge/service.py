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
    2. Private — only visible within the same account.
    3. Public + approved — visible if target_role matches.
    4. Public + not approved — visible only to management within same account.
    """
    vis = article.get("visibility", "private")
    target = article.get("target_role", "all")
    approved = bool(article.get("approved", 1))

    if article.get("created_by") == user_id:
        return True

    if vis == "private":
        return article.get("account_id") == account_id

    # public article
    if approved:
        return target == "all" or target == role

    # public but pending approval — only same-account management can see it
    if article.get("account_id") == account_id:
        return role in ("owner", "admin", "fleet", "safety")

    return False
