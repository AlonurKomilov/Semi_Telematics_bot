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

#: One line per failure. Enough to recognise it on a board; short enough
#: that nothing structured can hide inside.
_MESSAGE_CHARS = 300

_state: dict = {"started": None, "t0": None, "failures": []}


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
    if not _enabled():
        return
    _state["started"] = datetime.now(timezone.utc).isoformat()
    _state["t0"] = time.monotonic()
    # What was asked for. Empty means the whole suite, which is the only
    # kind of run a green verdict can be claimed from.
    _state["scope"] = " ".join(config.args or ())


def pytest_runtest_logreport(report):
    if not _enabled() or report.outcome != "failed":
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
