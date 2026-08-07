# Known baseline test failures — stop re-deriving these

Living ledger. If a run shows ONLY these, your change is clean.
Fix one → delete its row in the same commit.

| suite | count | cause | owner |
|---|---|---|---|
| test_backfill_aggregations (`test_m5_*` pair) | 2 | preflight treats a MagicMock as len()==0 and skips; pre-dates the warehouse arc. NOTE: since the reroll hook, fixing the preflight also requires patching via `features.vehicles.warehouse.aggregator` (late-bound — see test_rollups late-binding test) | warehouse (Claude session) |
| test_history_backfill (`test_backfill_*` trio) | 3 | same MagicMock-preflight class | warehouse (Claude session) |
| test_backfill_recovery | 2 | `reset_backfill_status` AttributeError — fake DB predates the method | warehouse (Claude session) |
| test_activity_trail | 1 | in-flight activity-trail work | co-dev |
| test_vehicles_registry (route trio) | 3 | fixture interplay with uncommitted activity-trail + platform DB not initialised in that fixture | co-dev fixture, vehicles suite |

Verified pre-existing against clean baseline worktrees during the
2026-08 warehouse arc (twice, independently, incl. by a review agent).
