# Known baseline test failures — stop re-deriving these

Living ledger. If a run shows ONLY these, your change is clean.
Fix one → delete its row in the same commit.

| suite | count | cause | owner |
|---|---|---|---|
| test_activity_trail | 1 | in-flight activity-trail work | co-dev |
| test_vehicles_registry (route trio) | 3 | fixture interplay with uncommitted activity-trail + platform DB not initialised in that fixture | co-dev fixture, vehicles suite |

Verified pre-existing against clean baseline worktrees during the
2026-08 warehouse arc (twice, independently, incl. by a review agent).
