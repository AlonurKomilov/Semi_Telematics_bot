"""AI tools package — auto-registers all tools on import."""

from ai.tools.registry import (                              # noqa: F401
    get_all_tool_schemas,
    get_tool_handler,
    get_tool_count,
    filter_tools_for_role,
    get_cached_vertex_tools,
    invalidate_tool_cache,
    execute_tool,
)

# Import domain modules so @register_tool decorators run
from ai.tools import faults as faults                        # noqa: F401
from ai.tools import vehicle as vehicle                      # noqa: F401
from ai.tools import health as health                        # noqa: F401
from ai.tools import fuel as fuel                            # noqa: F401
from ai.tools import efficiency as efficiency                # noqa: F401
from ai.tools import events as events                        # noqa: F401
from ai.tools import maintenance as maintenance              # noqa: F401
from ai.tools import geo as geo                              # noqa: F401
from ai.tools import camera as camera                        # noqa: F401

# Role-neutral alias
AI_TOOLS = get_all_tool_schemas()
