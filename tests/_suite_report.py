"""Report what this pytest run did, to the operator console's board.

Inert unless configured. With ``SUITE_REPORT_URL`` and
``SUITE_REPORT_TOKEN`` unset — which is every developer's default — this
plugin collects nothing and sends nothing, so a run costs exactly what it
cost before.

**It never writes to a database.** The suite runs against a template copy;
a test process holding a handle to the production one is the leak this
platform has already had (see the userdata incident). The summary travels
over HTTP, after the session is over, and a failure to send is logged and
swallowed: a board that cannot be reached must not turn a green run red.

**What it sends is a summary, never output.** Counts, the failing node
ids, the first line of each failure, and the facts that make a run
attributable: which commit, which branch, whether the tree was dirty, who
was at the keyboard, and what was asked for. No tracebacks — they carry
fixture values, and fixture values here carry customer-shaped data.

The dirty flag and the scope matter as much as the counts. Three sessions
and a person write to this tree: a red subset run on a tree with
uncommitted edits accuses nobody, and a board that renders it like a
clean full-suite failure would be worse than no board.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

_URL_ENV = "SUITE_REPORT_URL"
_TOKEN_ENV = "SUITE_REPORT_TOKEN"
_SOURCE_ENV = "SUITE_REPORT_SOURCE"      # "ci" when CI sets it; else "local"

#: The three keys this plugin may take from ``.env`` — and the only
#: three. A bare ``load_dotenv()`` here would put the whole file into the
#: test process, DATABASE_URL included, and the tests would run against
#: production: the exact leak this platform has already had once. So the
#: file is parsed for these names and nothing else is touched.
_DOTENV_KEYS = (_URL_ENV, _TOKEN_ENV, _SOURCE_ENV)


def _load_from_dotenv() -> None:
    """Fill the three keys from ``.env`` when the shell has not.

    The app reads ``.env`` at boot; a bare ``pytest`` reads nothing, so a
    token set in that file was invisible to the reporter and the board
    stayed empty while looking configured. A real environment variable
    always wins, so CI keeps control.
    """
    try:
        from tests._repo import REPO
        path = REPO / ".env"
        if not path.is_file():
            return
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in _DOTENV_KEYS and not os.getenv(key):
                os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:
        # A malformed .env is somebody else's problem; the reporter just
        # stays inert, which is its default anyway.
        pass

#: One line per failure. Enough to recognise it on a board; short enough
#: that nothing structured can hide inside.
_MESSAGE_CHARS = 300

_state: dict = {"started": None, "t0": None, "failures": [], "packages": {}}


def package_of(file: str) -> str:
    """Which feature or service a test file belongs to.

    The repo's own law decides this, not a guess: a package owns its
    tests in its own ``tests/`` subfolder, so everything before
    ``/tests/`` IS the package. ``features/settings/team_management``
    and ``capabilities/platform/billing`` come out at their real depth
    rather than being flattened to two segments, because that is where
    their tests actually live.
    """
    if "/tests/" in file:
        return file.split("/tests/")[0]
    if file.startswith("tests/"):
        return "tests"                      # the repo-wide guards
    return file.rsplit("/", 1)[0] or "?"


def _enabled() -> bool:
    return bool(os.getenv(_URL_ENV) and os.getenv(_TOKEN_ENV))


def _git(*args: str) -> str:
    try:
        out = subprocess.run(("git", *args), capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _actor() -> str:
    """Who was at the keyboard. The git identity first — it is what the
    commits will carry — then the shell user."""
    return (_git("config", "user.name") or os.getenv("USER")
            or os.getenv("USERNAME") or "")


def pytest_configure(config):
    _load_from_dotenv()
    if not _enabled():
        return
    _state["started"] = datetime.now(timezone.utc).isoformat()
    _state["t0"] = time.monotonic()
    # What was asked for. Empty means the whole suite, which is the only
    # kind of run a green verdict can be claimed from.
    _state["scope"] = " ".join(config.args or ())


def pytest_runtest_logreport(report):
    if not _enabled():
        return
    # Count every outcome per package, not just the failures: a board
    # that can say "features/loads, 43 passed" is one a reader can use
    # the way the repo is laid out. Counted on the CALL phase so a test
    # is counted once; a setup error has no call phase and is counted
    # there instead.
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        pkg = package_of(report.nodeid.split("::", 1)[0])
        row = _state["packages"].setdefault(pkg, {"passed": 0, "failed": 0, "skipped": 0})
        key = report.outcome if report.outcome in row else "failed"
        row[key] += 1

    if report.outcome != "failed":
        return
    if report.when not in ("call", "setup"):
        return
    message = ""
    try:
        text = str(report.longrepr or "")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("E "):
                message = line[2:].strip()
                break
        if not message:
            message = text.strip().splitlines()[-1] if text.strip() else ""
    except Exception:
        message = ""
    _state["failures"].append({
        "nodeid": report.nodeid,
        "file": report.nodeid.split("::", 1)[0],
        "message": message[:_MESSAGE_CHARS],
    })


def pytest_sessionfinish(session, exitstatus):
    if not _enabled() or _state["started"] is None:
        return
    # xdist: only the controller reports, or twelve workers each send a
    # partial run and the board shows twelve runs for one pytest.
    if getattr(session.config, "workerinput", None) is not None:
        return

    stats = getattr(session.config, "_suite_stats", None)
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    if tr is not None:
        counts["passed"] = len(tr.stats.get("passed", []))
        counts["failed"] = len(tr.stats.get("failed", []))
        counts["skipped"] = len(tr.stats.get("skipped", []))
        counts["errors"] = len(tr.stats.get("error", []))
    elif stats:
        counts.update(stats)

    payload = {
        "started_at": _state["started"],
        "source": os.getenv(_SOURCE_ENV, "local"),
        "actor": _actor(),
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # A red run on a dirty tree accuses nobody — the board needs to
        # be able to say so.
        "dirty": bool(_git("status", "--porcelain")),
        "scope": _state.get("scope", ""),
        "duration_s": round(time.monotonic() - _state["t0"], 2),
        **counts,
        # Deduped: xdist can log the same nodeid twice when a worker
        # crashes and the controller re-reports it.  Capped at what the
        # ingest accepts, and the count above still says the true total —
        # so a catastrophic run reads as "800 failed" with 500 named,
        # never as 500.
        "failures": list({f["nodeid"]: f
                          for f in _state["failures"]}.values())[:500],
        # One row per package the run actually touched. A package absent
        # from this list was not exercised, which is different from
        # passing — the board must be able to tell those apart.
        "packages": [{"package": k, **v} for k, v in
                     sorted(_state["packages"].items())][:200],
    }
    _send(payload)


def _send(payload: dict) -> None:
    """Best-effort POST. Never raises, never prints a traceback: a board
    that is down must not change what the developer sees."""
    import json
    import urllib.error
    import urllib.request

    url = os.getenv(_URL_ENV, "")
    token = os.getenv(_TOKEN_ENV, "")
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Suite-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception as exc:            # noqa: BLE001 — see the docstring
        print(f"\n[suite-report] not sent ({type(exc).__name__}: {exc}) — "
              f"the run itself is unaffected")
