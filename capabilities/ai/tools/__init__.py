"""AI tools package — auto-registers all tools on import."""

from capabilities.ai.tools.registry import (                              # noqa: F401
    get_all_tool_schemas,
    get_tool_handler,
    get_tool_count,
    filter_tools_for_role,
    get_cached_vertex_tools,
    get_anthropic_tools,
    invalidate_tool_cache,
    execute_tool,
)

# Import domain modules so @register_tool decorators run
from features.vehicles.faults import tool as faults                       # noqa: F401
from capabilities.ai.tools import vehicle as vehicle                      # noqa: F401
from features.vehicles.health import tool as health                       # noqa: F401
from features.vehicles.fuel import tool as fuel                           # noqa: F401
from features.vehicles.efficiency import tool as efficiency               # noqa: F401
from features.events import tool as events                                # noqa: F401
from capabilities.ai.tools import maintenance as maintenance              # noqa: F401
from capabilities.ai.tools import geo as geo                              # noqa: F401
from features.vehicles.cameras import tool as camera                      # noqa: F401
from capabilities.ai.tools import odometer as odometer                    # noqa: F401
from capabilities.ai.tools import drivers as drivers                      # noqa: F401
from capabilities.ai.tools import knowledge as knowledge                  # noqa: F401
from capabilities.ai.tools import idle as idle                            # noqa: F401
from capabilities.ai.tools import hos as hos                              # noqa: F401
from capabilities.ai.tools import alert_history as alert_history          # noqa: F401
from capabilities.ai.tools import work_orders as work_orders              # noqa: F401
from capabilities.ai.tools import inspections as inspections              # noqa: F401
from capabilities.ai.tools import history as history                      # noqa: F401

# Role-neutral alias
AI_TOOLS = get_all_tool_schemas()
