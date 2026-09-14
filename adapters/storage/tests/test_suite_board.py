"""The board's one real question: when did this go red, and on whose commit?

Counting passes is the easy half and nobody needs a board for it. The
half that cost half an hour this morning — reading ten failures and
working out which three were mine — is attribution, and attribution is a
question about the runs BEFORE this one.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


async def _run(db, *, sha, failures=(), passed=100, actor="alice", **kw):
    return await db.record_suite_run(
        started_at="2026-09-14T10:00:00+00:00",
        git_sha=sha, actor=actor, passed=passed,
        failed=len(failures),
        failures=[{"nodeid": n, "file": n.split("::")[0], "message": "boom"}
                  for n in failures],
        **kw,
    )


async def test_a_run_is_stored_with_what_it_was(db):
    run_id = await _run(db, sha="aaa111", actor="bob", source="ci",
                        scope="", passed=5000, failures=())
    run = await db.get_suite_run(run_id)
    assert run["git_sha"] == "aaa111" and run["actor"] == "bob"
    assert run["source"] == "ci" and run["passed"] == 5000
    assert run["failures"] == []


async def test_the_first_red_run_is_its_own_bracket(db):
    """Nothing before it, so the break is somewhere at or before this
    commit and the board says only that."""
    r1 = await _run(db, sha="aaa111", failures=("t.py::test_a",))
    run = await db.get_suite_run(r1)
    (f,) = run["failures"]
    assert f["first_seen_run"] == r1


async def test_a_test_that_stays_red_keeps_pointing_at_the_day_it_broke(db):
    """The property that makes the board readable a week later: a test
    red since Tuesday must not re-date itself to today on every run."""
    r1 = await _run(db, sha="aaa111", failures=("t.py::test_a",))
    r2 = await _run(db, sha="bbb222", failures=("t.py::test_a",))
    r3 = await _run(db, sha="ccc333", failures=("t.py::test_a",))
    newest = await db.get_suite_run(r3)
    (f,) = newest["failures"]
    assert f["first_seen_run"] == r1, "it re-dated itself to the latest run"
    assert f["first_seen_sha"] == "aaa111"
    assert r2 != f["first_seen_run"]


async def test_a_green_run_then_a_red_one_brackets_the_change(db):
    """THE question. The last run that passed it and the first that
    failed it name the two commits the break lies between — narrow
    enough to read, and it accuses nobody of more than that."""
    await _run(db, sha="green01", failures=())
    r_red = await _run(db, sha="red0002", failures=("t.py::test_a",))
    run = await db.get_suite_run(r_red)
    (f,) = run["failures"]
    assert f["first_seen_run"] == r_red
    assert f["last_green_sha"] == "green01", (
        "the near side of the bracket is missing — the board can say "
        "'it is red' but not 'it went red here'"
    )


async def test_fixed_then_broken_again_starts_a_NEW_bracket(db):
    """A second break is not the first one continuing. Carrying the old
    date forward would point a reader at a commit that was already
    proven innocent by the green run in between."""
    r1 = await _run(db, sha="aaa111", failures=("t.py::test_a",))
    await _run(db, sha="fix0001", failures=())          # green
    r3 = await _run(db, sha="ccc333", failures=("t.py::test_a",))
    run = await db.get_suite_run(r3)
    (f,) = run["failures"]
    assert f["first_seen_run"] == r3 and f["first_seen_run"] != r1
    assert f["last_green_sha"] == "fix0001"


async def test_history_answers_red_since_when(db):
    r1 = await _run(db, sha="aaa111", failures=("t.py::test_a",), actor="alice")
    await _run(db, sha="bbb222", failures=("t.py::test_b",), actor="bob")
    r3 = await _run(db, sha="ccc333", failures=("t.py::test_a",), actor="carol")

    history = await db.suite_failure_history("t.py::test_a")
    assert [h["id"] for h in history] == [r3, r1], "newest first, and only this test"
    assert {h["actor"] for h in history} == {"alice", "carol"}


async def test_a_subset_run_on_a_dirty_tree_says_both(db):
    """Three sessions write to this tree. A red subset on a dirty tree
    accuses nobody, and the board can only refuse to accuse if the
    reporter told it."""
    from adapters.storage.suite_runs import summarise

    run_id = await _run(db, sha="aaa111", failures=("t.py::test_a",),
                        dirty=True, scope="features/loads")
    run = await db.get_suite_run(run_id)
    verdict = summarise(run)
    assert verdict["ok"] is False
    assert verdict["partial"] is True, "a subset must not read as the whole suite"
    assert verdict["dirty"] is True


async def test_a_green_full_run_is_the_only_unqualified_green(db):
    from adapters.storage.suite_runs import summarise

    run_id = await _run(db, sha="aaa111", failures=(), scope="", dirty=False)
    verdict = summarise(await db.get_suite_run(run_id))
    assert verdict == {**verdict, "ok": True, "partial": False, "dirty": False}


async def test_runs_are_listed_newest_first(db):
    ids = [await _run(db, sha=f"sha{i:04d}") for i in range(4)]
    listed = await db.list_suite_runs(limit=3)
    assert [r["id"] for r in listed] == list(reversed(ids))[:3]


async def test_pruning_takes_the_failures_with_the_run(db):
    """A run without its failures is a row that says 'red' and cannot
    say why."""
    await _run(db, sha="aaa111", failures=("t.py::test_a",))
    dropped = await db.prune_suite_runs(keep_days=0)
    assert dropped >= 1
    assert await db.list_suite_runs() == []
    # Asked of the TABLE, not of suite_failure_history: that reader joins
    # suite_runs, so a failure row orphaned by a prune is invisible to it
    # and the assertion passes while the rows pile up forever.
    cur = await db._db.execute("SELECT COUNT(*) AS n FROM suite_failures")
    left = dict(await cur.fetchone())["n"]
    assert left == 0, f"{left} orphaned failure row(s) survived the prune"


async def test_recent_runs_survive_a_prune(db):
    run_id = await _run(db, sha="aaa111", failures=("t.py::test_a",))
    assert await db.prune_suite_runs(keep_days=90) == 0
    assert [r["id"] for r in await db.list_suite_runs()] == [run_id]
