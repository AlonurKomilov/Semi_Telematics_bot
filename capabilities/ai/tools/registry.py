"""Tool registry: auto-registration via decorator, role-based filtering."""

from __future__ import annotations

import logging
import time as _time
from typing import Any, Callable, Coroutine

logger = logging.getLogger("bot.ai.tools")

# Central registry: tool_name → {"schema": {...}, "handler": async fn}
_TOOL_REGISTRY: dict[str, dict[str, Any]] = {}

# Advertised-tool caches, keyed by (account_id, role, scoped) → (expires_at, list).
# Keyed by account (not just role) so a per-account permission override or a
# disabled module is reflected in what the model is even shown; TTL-bounded so a
# worker that misses an invalidation signal still ages out within the window.
_TOOLS_CACHE_TTL_S = 300
_cached_tools: dict[tuple, tuple[float, list]] = {}


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


async def filter_tools_for_role(
    role_str: str | None,
    account_id: int | None = None,
    scoped: bool = False,
) -> list[dict]:
    """Return the subset of tool schemas a user may actually use.

    **Account-aware:** when ``account_id`` is given, permissions resolve via
    ``get_account_permissions`` — the same per-account Permissions matrix +
    module masking the runtime gate uses — so a revoked feature's tools
    disappear from what the model is shown (not just blocked at call time).
    Falls back to role defaults when no account_id is available.

    **Scope-aware:** when ``scoped`` (a vehicle/company-restricted user), the
    account-wide aggregate tools are dropped too, since the gate blocks them
    for that user anyway — no point advertising them.
    """
    from capabilities.iam.permissions import TOOL_PERMISSIONS, ACCOUNT_WIDE_TOOLS

    all_schemas = get_all_tool_schemas()
    if not role_str:
        return all_schemas
    try:
        from adapters.storage import Role
        role = Role(role_str)
        if account_id is not None:
            from capabilities.iam.permissions import get_account_permissions
            perms = await get_account_permissions(role, int(account_id))
        else:
            from capabilities.iam.permissions import get_permissions
            perms = get_permissions(role)
    except (ValueError, KeyError, ImportError):
        return all_schemas

    filtered = []
    for tool_def in all_schemas:
        name = tool_def["name"]
        if scoped and name in ACCOUNT_WIDE_TOOLS:
            continue  # the gate blocks fleet-wide tools for scoped users
        required = TOOL_PERMISSIONS.get(name)
        if required is None:
            filtered.append(tool_def)
        elif any(getattr(perms, p, False) for p in required):
            filtered.append(tool_def)
    return filtered


def _cache_hit(cache: dict, key: tuple):
    entry = cache.get(key)
    if entry is not None and entry[0] > _time.monotonic():
        return entry[1]
    return None


async def get_cached_vertex_tools(
    role: str | None = None,
    account_id: int | None = None,
    scoped: bool = False,
):
    """Return cached google-genai Tool objects, filtered for the user."""
    key = (account_id, role, scoped)
    hit = _cache_hit(_cached_tools, key)
    if hit is not None:
        return hit
    from google.genai import types as _gtypes
    tool_defs = await filter_tools_for_role(role, account_id, scoped)
    func_decls = [_gtypes.FunctionDeclaration(**td) for td in tool_defs]
    result = [_gtypes.Tool(function_declarations=func_decls)]
    _cached_tools[key] = (_time.monotonic() + _TOOLS_CACHE_TTL_S, result)
    return result


# Anthropic-format tool cache (different schema shape than Gemini —
# Anthropic uses ``input_schema`` instead of ``parameters``).
_anthropic_tools_cache: dict[tuple, tuple[float, list[dict]]] = {}


async def get_anthropic_tools(
    role: str | None = None,
    account_id: int | None = None,
    scoped: bool = False,
) -> list[dict]:
    """Return Anthropic-format tool definitions filtered for the user.

    Anthropic's tool-use API expects ``{name, description, input_schema}``
    where Gemini uses ``{name, description, parameters}``.  Same JSON
    Schema body, just a different field name.
    """
    key = (account_id, role, scoped)
    hit = _cache_hit(_anthropic_tools_cache, key)
    if hit is not None:
        return hit
    tool_defs = await filter_tools_for_role(role, account_id, scoped)
    converted = [
        {
            "name": td["name"],
            "description": td["description"],
            "input_schema": td.get("parameters", {"type": "object", "properties": {}}),
        }
        for td in tool_defs
    ]
    _anthropic_tools_cache[key] = (_time.monotonic() + _TOOLS_CACHE_TTL_S, converted)
    return converted


def invalidate_tool_cache(account_id: int | None = None):
    """Clear cached advertised-tool lists.

    Call with no argument after changing tool registrations; call with an
    ``account_id`` after that account's permissions/modules change so the
    next request re-resolves the advertised list from fresh perms.
    """
    if account_id is None:
        _cached_tools.clear()
        _anthropic_tools_cache.clear()
        return
    for cache in (_cached_tools, _anthropic_tools_cache):
        for k in [k for k in cache if k[0] == account_id]:
            del cache[k]


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
