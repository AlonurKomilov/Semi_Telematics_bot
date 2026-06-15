"""Knowledge-base AI tool — search uploaded SOPs, policies, and documents.

Account-scoped reads (``account_id``) with no per-vehicle dimension; open to
all roles (no feature-flag gate), so no Vehicle-Access filtering applies.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


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
                "description": "Optional category filter, e.g. 'safety', 'maintenance', 'compliance'",
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
        "articles": [
            {
                "title": a.get("title", ""),
                "category": a.get("category", ""),
                # Full body so the model can quote / paraphrase directly.
                "description": a.get("description", ""),
                "tags": a.get("tags", ""),
                "pinned": bool(a.get("pinned")),
                # For pdf / video / image the model can't read the file —
                # it should surface ``media_url`` to the user as a link.
                "media_type": a.get("media_type", "link"),
                "media_url": a.get("media_url", ""),
            }
            for a in articles[:8]
        ],
    }
