"""Tool registry: auto-registration via decorator, role-based filtering."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger("bot.ai.tools")

# Central registry: tool_name → {"schema": {...}, "handler": async fn}
_TOOL_REGISTRY: dict[str, dict[str, Any]] = {}

# Vertex AI cached tool objects per role
_cached_tools: dict[str | None, list] = {}


def register_tool(schema: dict):
    """Decorator — registers a tool schema + its async handler function."""
    def decorator(func: Callable[..., Coroutine]):
        _TOOL_REGISTRY[schema["name"]] = {
            "schema": schema,
            "handler": func,
        }
        return func
    return decorator


def get_all_tool_schemas() -> list[dict]:
    """Return all registered tool JSON schemas."""
    return [entry["schema"] for entry in _TOOL_REGISTRY.values()]


def get_tool_handler(name: str) -> Callable[..., Coroutine] | None:
    """Return the async handler for a tool, or None if unknown."""
    entry = _TOOL_REGISTRY.get(name)
    return entry["handler"] if entry else None


def get_tool_count() -> int:
    """Return the number of registered tools."""
    return len(_TOOL_REGISTRY)


def filter_tools_for_role(role_str: str | None) -> list[dict]:
    """Return the subset of tool schemas the given role can use."""
    from capabilities.iam.permissions import TOOL_PERMISSIONS

    all_schemas = get_all_tool_schemas()
    if not role_str:
        return all_schemas
    try:
        from capabilities.iam.permissions import get_permissions
        from adapters.storage import Role
        role = Role(role_str)
        perms = get_permissions(role)
    except (ValueError, KeyError, ImportError):
        return all_schemas

    filtered = []
    for tool_def in all_schemas:
        required = TOOL_PERMISSIONS.get(tool_def["name"])
        if required is None:
            filtered.append(tool_def)
        elif any(getattr(perms, p, False) for p in required):
            filtered.append(tool_def)
    return filtered


def get_cached_vertex_tools(role: str | None = None):
    """Return cached Vertex AI Tool objects, filtered by role."""
    if role in _cached_tools:
        return _cached_tools[role]
    from vertexai.generative_models import Tool, FunctionDeclaration
    tool_defs = filter_tools_for_role(role)
    func_decls = [FunctionDeclaration(**td) for td in tool_defs]
    result = [Tool(function_declarations=func_decls)]
    _cached_tools[role] = result
    return result


def invalidate_tool_cache():
    """Clear cached tool objects — call after modifying tool registrations."""
    _cached_tools.clear()


async def execute_tool(tool_name: str, tool_args: dict,
                       samsara_client,
                       account_id: int | None = None,
                       db=None) -> dict:
    """Execute a registered tool by name. Returns result dict."""
    handler = get_tool_handler(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return await handler(tool_args, samsara_client,
                             account_id=account_id, db=db)
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"error": str(e)}
