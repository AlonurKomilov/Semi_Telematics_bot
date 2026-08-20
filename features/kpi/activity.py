"""KPI — activity-trail entity declaration (see the registry).

Incentive runs decide what people are PAID, so every mutation that can
move a payout writes a trail event on the run: created, row inputs
edited (day marks, extras), override set/cleared, finalized, discarded.
Viewing follows the page's own gate (can_kpi); no restore — a
discarded draft is regenerable from its period, not restorable.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "kpi_run", "KPI run", "kpi",
    view_permissions=("can_kpi",),
))
