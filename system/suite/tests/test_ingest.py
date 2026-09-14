"""The board, end to end: a reporter posts, an operator reads.

Two guarantees worth the round trip. The ingest is the one ``/system/*``
route a person does not open — a finished pytest process does, with a
shared secret — so the token is the whole wall and it is tested from
outside. And the reads are the operator's, so they must refuse the
reporter's token as firmly as the ingest refuses a session.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "x" * 32)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

TOKEN = "a-shared-secret-for-the-reporter"


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    monkeypatch.setenv("SUITE_REPORT_TOKEN", TOKEN)
    from interfaces.api.app import create_api
    from interfaces.api.deps import require_system_owner
    app = create_api()
    app.dependency_overrides[require_system_owner] = lambda: {"sub": "1", "role": "owner"}
    return app, pg_db


def _run_body(**over):
    body = {
        "started_at": "2026-09-14T10:00:00+00:00",
        "source": "local", "actor": "alice",
        "git_sha": "abc1234", "git_branch": "main",
        "dirty": False, "scope": "",
        "passed": 5100, "failed": 0, "skipped": 16, "errors": 0,
        "duration_s": 951.4, "failures": [],
    }
    body.update(over)
    return body


async def _post(app, body, token=TOKEN):
    headers = {"X-Suite-Token": token} if token is not None else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/api/system/suite/runs", json=body, headers=headers)


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


# ── the wall ─────────────────────────────────────────────────────────

async def test_the_ingest_refuses_a_missing_token(api):
    app, _ = api
    assert (await _post(app, _run_body(), token=None)).status_code == 401


async def test_the_ingest_refuses_a_wrong_token(api):
    app, _ = api
    assert (await _post(app, _run_body(), token="not-it")).status_code == 401


async def test_an_unset_token_closes_the_ingest_rather_than_opening_it(api, monkeypatch):
    """The failure mode that would matter: a board nobody configured must
    not take runs from whoever finds the URL."""
    app, _ = api
    monkeypatch.delenv("SUITE_REPORT_TOKEN", raising=False)
    assert (await _post(app, _run_body(), token="")).status_code == 401
    assert (await _post(app, _run_body(), token=TOKEN)).status_code == 401


# ── the round trip ───────────────────────────────────────────────────

async def test_a_reported_run_reaches_the_operators_board(api):
    app, db = api
    r = await _post(app, _run_body())
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]

    listed = (await _get(app, "/api/system/suite/runs")).json()
    assert listed["count"] == 1
    row = listed["items"][0]
    assert row["id"] == run_id
    assert row["ok"] is True and row["partial"] is False and row["dirty"] is False
    assert row["actor"] == "alice" and row["git_sha"] == "abc1234"


async def test_a_failure_arrives_with_the_bracket_that_names_the_suspects(api):
    """The board's reason to exist: green, then red, and the two commits
    the break lies between."""
    app, _ = api
    await _post(app, _run_body(git_sha="green01"))
    red = await _post(app, _run_body(
        git_sha="red0002", passed=5099, failed=1,
        failures=[{"nodeid": "tests/test_x.py::test_y",
                   "file": "tests/test_x.py", "message": "AssertionError: nope"}],
    ))
    detail = (await _get(app, f"/api/system/suite/runs/{red.json()['id']}")).json()
    assert detail["ok"] is False
    (f,) = detail["failures"]
    assert f["nodeid"] == "tests/test_x.py::test_y"
    assert f["message"].startswith("AssertionError")
    assert f["last_green_sha"] == "green01", "the board cannot say where it went red"
    assert f["first_seen_sha"] == "red0002"


async def test_history_says_red_since_when(api):
    app, _ = api
    fail = [{"nodeid": "t.py::t", "file": "t.py", "message": "boom"}]
    await _post(app, _run_body(git_sha="aaa", failed=1, failures=fail))
    await _post(app, _run_body(git_sha="bbb"))
    await _post(app, _run_body(git_sha="ccc", failed=1, failures=fail))

    hist = (await _get(app, "/api/system/suite/failures?nodeid=t.py::t")).json()
    assert [h["git_sha"] for h in hist["items"]] == ["ccc", "aaa"]


async def test_a_subset_run_on_a_dirty_tree_is_labelled_as_one(api):
    """Three sessions write to this tree; a red subset on a dirty tree
    accuses nobody, and the row has to say so."""
    app, _ = api
    await _post(app, _run_body(scope="features/loads", dirty=True,
                               passed=30, failed=1,
                               failures=[{"nodeid": "a::b", "file": "a",
                                          "message": "x"}]))
    row = (await _get(app, "/api/system/suite/runs")).json()["items"][0]
    assert row["partial"] is True and row["dirty"] is True and row["ok"] is False


async def test_a_bad_source_is_refused(api):
    """``ci`` and ``local`` are different evidence; a third word would be
    a column nobody can read."""
    app, _ = api
    assert (await _post(app, _run_body(source="whatever"))).status_code == 422


async def test_the_board_reads_are_the_operators_not_the_reporters(api):
    """The ingest's token opens the ingest and nothing else — the reads
    are gated by require_system_owner, which this fixture overrides, so
    the assertion here is that the token is not a second key to them."""
    app, _ = api
    from interfaces.api.deps import require_system_owner
    app.dependency_overrides.pop(require_system_owner, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/system/suite/runs", headers={"X-Suite-Token": TOKEN})
    assert r.status_code in (401, 403), r.status_code


# ── per package, over the wire ────────────────────────────────────────

async def test_packages_arrive_and_answer_from_the_run_that_ran_them(api):
    """The view the operator actually asked for: features and services,
    each speaking from the last run that exercised it."""
    app, _ = api
    await _post(app, _run_body(git_sha="aaa111", passed=65, packages=[
        {"package": "features/loads", "passed": 40, "failed": 0, "skipped": 0},
        {"package": "features/vehicles", "passed": 25, "failed": 0, "skipped": 0},
    ]))
    await _post(app, _run_body(git_sha="bbb222", scope="features/vehicles",
                               passed=24, failed=1, packages=[
        {"package": "features/vehicles", "passed": 24, "failed": 1, "skipped": 0},
    ], failures=[{"nodeid": "features/vehicles/tests/test_a.py::test_x",
                  "file": "features/vehicles/tests/test_a.py", "message": "boom"}]))

    rows = {r["package"]: r for r in
            (await _get(app, "/api/system/suite/packages")).json()["items"]}
    assert rows["features/vehicles"]["failed"] == 1
    assert rows["features/vehicles"]["git_sha"] == "bbb222"
    assert rows["features/loads"]["git_sha"] == "aaa111", (
        "loads answered from a run that never exercised it"
    )


async def test_a_packages_drawer_shows_its_own_failures_and_its_own_runs(api):
    app, _ = api
    await _post(app, _run_body(git_sha="aaa111", passed=1, failed=2, packages=[
        {"package": "features/loads", "passed": 1, "failed": 1, "skipped": 0},
        {"package": "features/vehicles", "passed": 0, "failed": 1, "skipped": 0},
    ], failures=[
        {"nodeid": "features/loads/tests/test_a.py::test_x",
         "file": "features/loads/tests/test_a.py", "message": "loads boom"},
        {"nodeid": "features/vehicles/tests/test_b.py::test_y",
         "file": "features/vehicles/tests/test_b.py", "message": "vehicles boom"},
    ]))
    detail = (await _get(app, "/api/system/suite/packages/features/loads")).json()
    assert detail["package"] == "features/loads"
    assert [f["nodeid"] for f in detail["failures"]] == [
        "features/loads/tests/test_a.py::test_x"], "it was handed another package's red"
    assert len(detail["history"]) == 1


async def test_a_package_nobody_ran_is_absent_from_the_board(api):
    app, _ = api
    await _post(app, _run_body(packages=[
        {"package": "features/loads", "passed": 40, "failed": 0, "skipped": 0}]))
    names = {r["package"] for r in
             (await _get(app, "/api/system/suite/packages")).json()["items"]}
    assert names == {"features/loads"}
