"""Knowledge Base — /tips command and callback handlers for browsing articles.

System-level feature: shows articles from the platform DB.
Private articles show only within the user's account.
Public articles are visible cross-account (optionally role-filtered).
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from adapters.storage.knowledge import KB_CATEGORIES
from features.knowledge.service import can_view_article as _can_view_article_fn
from interfaces.bot.state import get_platform_db
from interfaces.bot.helpers import _show
from interfaces.bot.auth import _require_registered


# ── Main menu ─────────────────────────────────────────────────────

@_require_registered
async def cmd_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show knowledge base category menu."""
    user = context.user_data["_db_user"]
    platform = get_platform_db()
    role = user.role.value
    cats = await platform.get_kb_categories(user.account_id, user_role=role, user_id=user.telegram_id)
    cat_counts = {c["category"]: c["count"] for c in cats}
    total = sum(cat_counts.values())

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📚  <b>Knowledge Base</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {total} article{'s' if total != 1 else ''} available\n"
        "\n"
        "  Tap a category to browse:\n"
    )

    buttons = []
    for key, label in KB_CATEGORIES.items():
        count = cat_counts.get(key, 0)
        if count > 0:
            buttons.append([InlineKeyboardButton(
                f"{label} ({count})", callback_data=f"kb_cat:{key}"
            )])

    if not buttons:
        text += "\n  No articles yet."
        if user.role.value in ("owner", "admin", "fleet", "safety"):
            text += "\n  Add articles from the dashboard."

    buttons.append([InlineKeyboardButton("🔍 Search", callback_data="kb_search")])
    buttons.append([InlineKeyboardButton("📌 Pinned", callback_data="kb_pinned")])
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_start")])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


# ── Category listing ──────────────────────────────────────────────

async def cmd_kb_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show articles in a specific category."""
    query = update.callback_query
    if query:
        await query.answer()

    user = context.user_data.get("_db_user")
    if not user:
        return

    data = query.data if query else ""
    category = data.replace("kb_cat:", "") if data.startswith("kb_cat:") else "general"
    cat_label = KB_CATEGORIES.get(category, "📚 General")

    platform = get_platform_db()
    role = user.role.value
    articles = await platform.get_kb_articles(
        user.account_id, user_role=role, user_id=user.telegram_id, category=category,
    )

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {cat_label}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
    )

    if not articles:
        text += "\n  No articles in this category."

    buttons = []
    for a in articles[:20]:
        pin = "📌 " if a.get("pinned") else ""
        media_icon = _media_icon(a.get("media_type", ""))
        buttons.append([InlineKeyboardButton(
            f"{pin}{media_icon}{a['title'][:40]}",
            callback_data=f"kb_art:{a['id']}"
        )])

    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_tips")])
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


# ── Pinned articles ───────────────────────────────────────────────

async def cmd_kb_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pinned/featured articles."""
    query = update.callback_query
    if query:
        await query.answer()

    user = context.user_data.get("_db_user")
    if not user:
        return

    platform = get_platform_db()
    role = user.role.value
    articles = await platform.get_kb_articles(
        user.account_id, user_role=role, user_id=user.telegram_id, pinned_only=True,
    )

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📌  <b>Pinned Articles</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
    )

    if not articles:
        text += "\n  No pinned articles yet."

    buttons = []
    for a in articles[:20]:
        media_icon = _media_icon(a.get("media_type", ""))
        cat_label = KB_CATEGORIES.get(a.get("category", ""), "")
        buttons.append([InlineKeyboardButton(
            f"{media_icon}{a['title'][:40]}",
            callback_data=f"kb_art:{a['id']}"
        )])

    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_tips")])
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


# ── Article detail ────────────────────────────────────────────────

async def cmd_kb_article(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a single article with details."""
    query = update.callback_query
    if query:
        await query.answer()

    user = context.user_data.get("_db_user")
    if not user:
        return

    data = query.data if query else ""
    try:
        article_id = int(data.replace("kb_art:", ""))
    except (ValueError, AttributeError):
        return

    platform = get_platform_db()
    article = await platform.get_kb_article(article_id)
    if not article:
        await _show(update, context, ["Article not found."])
        return

    if not _can_view_article(user, article):
        await _show(update, context, ["You don't have access to this article."])
        return

    cat_label = KB_CATEGORIES.get(article.get("category", "general"), "📚 General")
    media_icon = _media_icon(article.get("media_type", ""))
    pin = "📌 " if article.get("pinned") else ""

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {pin}{media_icon}<b>{article['title']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {cat_label}\n"
    )

    if article.get("description"):
        text += f"\n{article['description']}\n"

    if article.get("tags"):
        tags = " ".join(f"#{t.strip()}" for t in article["tags"].split(",") if t.strip())
        text += f"\n🏷 {tags}\n"

    buttons = []
    if article.get("media_url"):
        url = article["media_url"]
        mtype = article.get("media_type", "link")
        btn_label = {
            "video": "▶️ Watch Video",
            "pdf": "📄 Open PDF",
            "image": "🖼 View Image",
        }.get(mtype, "🔗 Open Link")
        buttons.append([InlineKeyboardButton(btn_label, url=url)])

    # Back to category
    buttons.append([InlineKeyboardButton(
        "◀️ Back",
        callback_data=f"kb_cat:{article.get('category', 'general')}"
    )])

    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


# ── Search prompt ─────────────────────────────────────────────────

async def cmd_kb_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for search query."""
    query = update.callback_query
    if query:
        await query.answer()

    context.user_data["_pending"] = "kb_search"
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🔍  <b>Search Knowledge Base</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Type your search query:\n"
    )
    buttons = [[InlineKeyboardButton("◀️ Cancel", callback_data="cmd_tips")]]
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))


async def handle_kb_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for knowledge base search. Returns True if handled."""
    if context.user_data.get("_pending") != "kb_search":
        return False

    context.user_data.pop("_pending", None)
    user = context.user_data.get("_db_user")
    if not user:
        return True

    search_term = update.message.text.strip()[:100]
    platform = get_platform_db()
    role = user.role.value
    articles = await platform.get_kb_articles(
        user.account_id, user_role=role, user_id=user.telegram_id, search=search_term,
    )

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  🔍  Results for \"{search_term}\"\n"
        "━━━━━━━━━━━━━━━━━━━\n"
    )

    if not articles:
        text += "\n  No articles found."

    buttons = []
    for a in articles[:20]:
        pin = "📌 " if a.get("pinned") else ""
        media_icon = _media_icon(a.get("media_type", ""))
        buttons.append([InlineKeyboardButton(
            f"{pin}{media_icon}{a['title'][:40]}",
            callback_data=f"kb_art:{a['id']}"
        )])

    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_tips")])
    await _show(update, context, [text], keyboard=InlineKeyboardMarkup(buttons))
    return True


# ── Helpers ───────────────────────────────────────────────────────

def _can_view_article(user, article: dict) -> bool:
    """Thin adapter: convert bot User object to the signature of the capability."""
    return _can_view_article_fn(
        user_id=user.telegram_id,
        account_id=user.account_id,
        role=user.role.value,
        article=article,
    )


def _media_icon(media_type: str) -> str:
    return {
        "video": "▶️ ",
        "pdf": "📄 ",
        "image": "🖼 ",
        "link": "🔗 ",
    }.get(media_type, "")
