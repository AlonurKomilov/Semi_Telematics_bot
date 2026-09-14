"""What the test suite did, and who was holding it when it broke.

Three sessions and a person write to this tree, so "the suite is red" is
never the useful sentence. It was red this morning with ten failures, and
working out which three were mine cost half an hour of reading — the
information existed nowhere, so it had to be reconstructed each time.
This is where it lives instead.

The question the board answers is **when a test went red**, not merely
that it is. Every run is stored, so a failing nodeid can be looked back
through history to the last run that PASSED it: the commit of that run
and the commit of the first red one bracket the change that broke it.
That bracket is the answer to "who broke what" — narrow enough to name
the suspects, honest enough not to name a culprit, because a subset run
on a dirty tree proves nothing about authorship.

Nothing here is written by a test. The suite runs against a template
copy of the database; a test process writing to the production one is
the leak this platform has already had once. A run summary arrives over
HTTP, after pytest has finished, from a reporter that is inert unless
explicitly configured.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: A run that asked for less than the whole suite is a different kind of
#: evidence: "30 passed" says nothing about the other five thousand. The
#: board keeps the scope so the two are never read as the same number.
FULL_SUITE_SCOPE = ""


class SuiteRunsMixin:
    """Reads and writes for the operator console's test board."""

    async def record_suite_run(
        self,
        *,
        started_at: str,
        source: str = "local",
        actor: str | None = None,
        git_sha: str | None = None,
        git_branch: str | None = None,
        dirty: bool = False,
        scope: str = FULL_SUITE_SCOPE,
        passed: int = 0,
        failed: int = 0,
        skipped: int = 0,
        errors: int = 0,
        duration_s: float | None = None,
        failures: Iterable[dict] | None = None,
    ) -> int:
        """Store one run and its failures; return the run id.

        The attribution is computed HERE rather than by the reporter: the
        reporter knows only its own run, and "when did this first go red"
        is a question about every run before it.
        """
        cur = await self._db.execute(
            """INSERT INTO suite_runs
                 (started_at, source, actor, git_sha, git_branch, dirty,
                  scope, passed, failed, skipped, errors, duration_s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING id""",
            (started_at, source, actor, git_sha, git_branch,
             1 if dirty else 0, scope, passed, failed, skipped, errors,
             duration_s),
        )
        row = await cur.fetchone()
        run_id = int(row[0] if not isinstance(row, dict) else row["id"])

        for f in failures or ():
            nodeid = str(f.get("nodeid", ""))[:500]
            if not nodeid:
                continue
            first_seen, last_green = await self._bracket(nodeid, run_id)
            await self._db.execute(
                """INSERT INTO suite_failures
                     (run_id, nodeid, file, message, first_seen_run,
                      last_green_sha)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, nodeid, str(f.get("file") or "")[:300],
                 str(f.get("message") or "")[:2000], first_seen, last_green),
            )
        await self._db.commit()
        return run_id

    async def _bracket(self, nodeid: str, run_id: int) -> tuple[int | None, str | None]:
        """(first run this nodeid was seen failing in, sha of the last run
        that passed it).

        "First seen" is carried forward from the previous failing run
        rather than recomputed, so a test red for a week keeps pointing at
        the day it broke instead of at today. A test that has since been
        fixed and broken again starts a new bracket, which is correct:
        that is a different break.
        """
        cur = await self._db.execute(
            """SELECT r.id, r.git_sha, f.id AS fail_id, f.first_seen_run
                 FROM suite_runs r
                 LEFT JOIN suite_failures f
                        ON f.run_id = r.id AND f.nodeid = ?
                WHERE r.id < ?
                ORDER BY r.id DESC
                LIMIT 40""",
            (nodeid, run_id),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return run_id, None                 # nothing before it: this run
        previous = rows[0]
        if previous.get("fail_id") is not None:
            # Still red — keep the original bracket.
            return (previous.get("first_seen_run") or previous["id"],
                    None)
        # It passed last time (or was not run). The newest run that did NOT
        # record this failure is the last known green, and its sha is the
        # near side of the bracket.
        return run_id, previous.get("git_sha")

    async def list_suite_runs(self, *, limit: int = 30) -> list[dict]:
        cur = await self._db.execute(
            """SELECT * FROM suite_runs
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_suite_run(self, run_id: int) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM suite_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        run = dict(row)
        cur = await self._db.execute(
            """SELECT f.*, r.git_sha AS first_seen_sha, r.finished_at AS first_seen_at,
                      r.actor AS first_seen_actor
                 FROM suite_failures f
                 LEFT JOIN suite_runs r ON r.id = f.first_seen_run
                WHERE f.run_id = ?
                ORDER BY f.nodeid""",
            (run_id,),
        )
        run["failures"] = [dict(r) for r in await cur.fetchall()]
        return run

    async def suite_failure_history(self, nodeid: str, *, limit: int = 20) -> list[dict]:
        """Every run that recorded this nodeid failing, newest first —
        the answer to "has this been red since Tuesday or since an hour
        ago"."""
        cur = await self._db.execute(
            """SELECT r.id, r.finished_at, r.git_sha, r.actor, r.source,
                      r.scope, f.message
                 FROM suite_failures f
                 JOIN suite_runs r ON r.id = f.run_id
                WHERE f.nodeid = ?
                ORDER BY r.id DESC LIMIT ?""",
            (nodeid, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def prune_suite_runs(self, keep_days: int) -> int:
        """Drop runs older than ``keep_days`` and their failures."""
        cur = await self._db.execute(
            "SELECT id FROM suite_runs WHERE finished_at < "
            "to_char(now() - (? || ' days')::interval, 'YYYY-MM-DD\"T\"HH24:MI:SS')",
            (str(int(keep_days)),),
        )
        ids = [int(dict(r)["id"]) for r in await cur.fetchall()]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        await self._db.execute(
            f"DELETE FROM suite_failures WHERE run_id IN ({marks})", ids)
        await self._db.execute(
            f"DELETE FROM suite_runs WHERE id IN ({marks})", ids)
        await self._db.commit()
        return len(ids)


def summarise(run: dict) -> dict[str, Any]:
    """The one-line verdict a board row shows.

    ``scope`` is part of the verdict, never decoration: a green subset
    run and a green full run are different claims, and a board that
    renders them the same teaches the reader to trust the wrong one.
    """
    failed = int(run.get("failed") or 0) + int(run.get("errors") or 0)
    return {
        "id": run.get("id"),
        "ok": failed == 0,
        "partial": bool(run.get("scope")),
        "dirty": bool(run.get("dirty")),
        "counts": {
            "passed": run.get("passed"),
            "failed": run.get("failed"),
            "skipped": run.get("skipped"),
            "errors": run.get("errors"),
        },
    }


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"))
