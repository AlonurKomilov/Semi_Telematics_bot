"""AI tools package — auto-registers all tools on import."""

from capabilities.ai.tools.registry import (                              # noqa: F401
    get_all_tool_schemas,
    get_tool_handler,
    get_tool_count,
    filter_tools_for_role,
    get_cached_vertex_tools,
    get_anthropic_tools,
    get_openai_tools,
    invalidate_tool_cache,
    execute_tool,
    tool_ok,
    tool_error,
)

# Pre-load the alerting capability FIRST.  Some feature packages' ``__init__``
# transitively touch it (e.g. ``features.parking`` → ``ai_vision`` →
# ``capabilities.alerting.pipeline``), and ``capabilities.alerting.__init__``
# in turn imports those features — a latent cycle that only breaks when a
# feature is imported before alerting is loaded.  Loading alerting here matches
# normal app-startup order so the feature ``ai_tool`` imports below resolve.
import capabilities.alerting  # noqa: F401,E402

# Import feature ``ai_tool`` modules so their @register_tool decorators run.
# Every tool definition lives in its feature (features/<x>/ai_tool.py); this
# package keeps only the mechanism (registry + scope helper) and this hub.
from features.vehicles.faults import ai_tool as faults                       # noqa: F401
from features.vehicles import ai_tool as vehicles_tools                       # noqa: F401
from features.location import ai_tool as location_tools                       # noqa: F401
from features.vehicles.health import ai_tool as health                       # noqa: F401
from features.overview import ai_tool as overview                            # noqa: F401
from features.vehicles.fuel import ai_tool as fuel                           # noqa: F401
from features.vehicles.efficiency import ai_tool as efficiency               # noqa: F401
from features.events import ai_tool as events                                # noqa: F401
from features.maintenance import ai_tool as maintenance                      # noqa: F401
from features.geofencing import ai_tool as geofencing                        # noqa: F401
from features.cameras import ai_tool as camera                      # noqa: F401
from features.drivers import ai_tool as drivers                              # noqa: F401
from features.knowledge import ai_tool as knowledge                          # noqa: F401
from features.parking import ai_tool as parking                              # noqa: F401
from capabilities.alerting import ai_tool as alerts                          # noqa: F401
from features.work_orders import ai_tool as work_orders                      # noqa: F401
from features.applications import ai_tool as applications              # noqa: F401
from features.inspections import ai_tool as pti                                      # noqa: F401

# Role-neutral alias
AI_TOOLS = get_all_tool_schemas()
