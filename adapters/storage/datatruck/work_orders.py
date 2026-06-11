"""``datatruck_work_orders`` storage mixin (stub).

Will own the upsert + read paths for shop work orders synced from
Datatruck.  Distinct from the existing first-party Work Orders
module — this one mirrors Datatruck's records so operators can see
both feeds side-by-side until a reconciliation strategy is chosen.
"""

from __future__ import annotations
