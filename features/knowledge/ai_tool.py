"""Knowledge-base AI tool — search uploaded SOPs, policies, and documents.

Account-scoped reads (``account_id``) with no per-vehicle dimension; open to
all roles (no feature-flag gate), so no Vehicle-Access filtering applies.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import (
    clip_untrusted, register_tool, untrusted_note,
)


from adapters.storage.knowledge import KB_CATEGORIES


#: How much of one article body reaches the model. Long enough for a
#: real procedure, short enough that eight of them cannot fill the
#: context — the tool used to ship up to 20 KB per article, re-sent on
#: every agent-loop round.
_BODY_MAX = 4000


@register_tool({
    "name": "search_knowledge_base",
    "description": (
        "Search the fleet's internal knowledge base for SOPs, policies, "
        "procedures, guides, and documents uploaded by the team. "
        "Use this when asked about company policies, procedures, how to do "
        "something, or any information that may be in uploaded documents. "
        "\n\nHandling the result: "
        "the ``description`` field is the article body in plain text — "
        "quote relevant passages directly to answer the user. "
        "When ``media_type`` is one of {pdf, video, image}, the binary "
        "behind ``media_url`` is NOT readable by you — tell the user the "
        "answer is in the linked document and include ``media_url`` so "
        "they can open it. "
        "For ``media_type`` = link, treat ``media_url`` as a complementary "
        "external reference, not the source of truth."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase (searches title, description, tags)",
            },
            "category": {
                "type": "string",
                # Derived, never retyped: a hand-copied list is a list
                # that drifts, and a category the store does not hold
                # matches nothing — which reads as "we have no article
                # on that" rather than "you asked for a category that
                # does not exist".
                "enum": sorted(KB_CATEGORIES),
                "description": (
                    "Optional category filter.  These are the only "
                    "values stored — a near miss matches nothing, and "
                    "nothing reads as 'we have no article on that'."
                ),
            },
        },
        "required": ["query"],
    },
})
async def search_knowledge_base(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    query = (tool_args.get("query") or "").strip()
    category = (tool_args.get("category") or "").strip() or None

    if not db or account_id is None:
        return {"error": "Knowledge base not available in this context"}
    if not query:
        return {"error": "A search query is required"}

    try:
        from infra.platform import get_platform_db as _get_pdb
        pdb = _get_pdb()
        articles = await pdb.get_kb_articles(
            account_id=account_id,
            category=category,
            search=query,
        )
    except Exception as e:
        return {"error": f"Knowledge base search failed: {e}"}

    if not articles:
        return {
            "query": query,
            "result": "No knowledge base articles found matching your search.",
        }

    return {
        "query": query,
        "count": len(articles),
        # A public article is readable by every account on the platform,
        # so an article body is text ANOTHER customer wrote — and the
        # comment below tells the model to quote it. Unbounded, that is
        # an instruction channel into every tenant's assistant: a body
        # can carry its own fake boundary and a line beginning "SYSTEM:".
        # Bodies are capped per article and the whole result is framed.
        "untrusted_note": untrusted_note(
            "knowledge-base article text, which any account on the "
            "platform can publish"
        ),
        "articles": [
            {
                "title": clip_untrusted(a.get("title"), 120),
                "category": a.get("category", ""),
                # The body, for quoting — capped, and flattened so it
                # cannot draw a boundary of its own.
                "description": clip_untrusted(a.get("description"), _BODY_MAX),
                "tags": clip_untrusted(a.get("tags"), 120),
                "pinned": bool(a.get("pinned")),
                # For pdf / video / image the model can't read the file —
                # it should surface ``media_url`` to the user as a link.
                "media_type": a.get("media_type", "link"),
                "media_url": a.get("media_url", ""),
            }
            for a in articles[:8]
        ],
    }
