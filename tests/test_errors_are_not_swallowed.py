"""A caught error either does something or says why it does not.

Four separate swallowed errors surfaced in one afternoon (2026-09-10),
written by four different authors, and every one of them cost the owner
a round trip to report a symptom neither he nor we could explain:

* ``chrome.sidePanel.open({tabId}).catch(() => {})`` — the panel refused
  to open, the rejection was discarded, and the card's button looked
  like it did nothing at all.
* three silent returns in the map card's inventory read — a card saying
  "2 items" and listing none looked identical whether the answer was in
  the air, refused, or never asked for.
* ``a.download = '4truck-extension.zip'`` overriding a correct server
  name, so every build landed as ``(8)``, ``(9)``, ``(10)``.
* a security aggregator whose first run "printed clean" because every
  rule raised and each raise was swallowed.

Same shape each time: **the program did not work, and looked exactly
like a program that did.**

**The rule is not "never swallow".** Most of these are deliberate and
correct — a best-effort telemetry post, a mount-time read whose failure
leaves a working default, a chain that must not reject. The rule is that
the decision has to be WRITTEN DOWN where it was made:

    } catch { /* the choice is lost, the session is not */ }   legal
    } catch { }                                               not
    .catch(() => {})                                          not
    except Exception:
        pass                                                  not

A comment costs one line and turns an invisible bug into a stated
trade-off that the next reader — or the next guard — can argue with.

**Two zones, because a repo-wide sweep is not available.** Four sessions
write to this tree at once, and editing 138 backend sites would collide
with all of them (see the five recorded index sweeps in
project_concurrent_codev_git). So:

* every user-facing frontend is **STRICT** — zero, and it is zero today;
* the backend is a **RATCHET** — today's counts, frozen, may only fall.

The ratchet is the shape this repo already uses for its eslint backlog
("today's exact counts, not a free pass"). It stops the bleeding without
a sweep, and every number below is a debt somebody can pay down.
"""
from __future__ import annotations

import pytest

from tests._repo import REPO
from tests._swallow import walk

#: Zero unexplained swallows, and it stays zero.  These are the surfaces
#: a customer touches; a silent failure here is a support call.
STRICT = (
    "interfaces/browser_extension/src",
    "interfaces/dashboard/src",
    "interfaces/miniapp/src",
    "interfaces/system_dashboard/src",
)

#: Exempt as a CLASS, with the reason that makes it a class.
EXEMPT: dict[str, str] = {
    "adapters/storage/migrations.py":
        "idempotent DDL: `try: ALTER … except: pass` IS the retry idiom — "
        "the column may already exist, and that is the expected case, not "
        "an error worth naming 101 times",
    "adapters/storage/platform_migrations.py":
        "same idiom, the Postgres twin",
}

#: Today's backend counts, per area.  A number may go DOWN — never up.
#: Measured 2026-09-10; each one is a debt, not a permission.
BUDGET: dict[str, int] = {
    "adapters/samsara": 1, "adapters/storage": 14,
    "capabilities/ai": 18, "capabilities/alerting": 4,
    "capabilities/data_lifecycle": 1, "capabilities/formatting": 1,
    "capabilities/integrations": 1, "capabilities/jobs": 1,
    "capabilities/localization": 1, "capabilities/permissions": 2,
    "capabilities/platform": 4, "capabilities/reporting": 1,
    "capabilities/source": 1, "conftest.py": 1,
    "features/applications": 19, "features/carrier_directory": 1,
    "features/drivers": 1, "features/geofencing": 1,
    "features/inspections": 5, "features/knowledge": 1,
    "features/kpi": 4, "features/maintenance": 7,
    "features/parking": 2, "features/scorecards": 2,
    "features/settings": 1, "features/vehicles": 3,
    "features/work_orders": 3, "infra/bot_registry.py": 3,
    "infra/error_reporter.py": 1, "infra/isolation.py": 2,
    "infra/scan_rescan.py": 1, "interfaces/api": 11,
    "interfaces/bot": 11, "run.py": 4,
    "scripts/host_snapshot.py": 1,
    "scripts/relocate_userdata_layout.py": 1,
    "scripts/sweep_legacy_perm_keys.py": 1,
}


def _area(path: str) -> str:
    return "/".join(path.split("/")[:2]) if "/" in path else path


@pytest.fixture(scope="module")
def found() -> dict[str, list[int]]:
    return walk(REPO)


def test_no_customer_facing_surface_swallows_in_silence(found):
    offenders = [
        f"{f}:{ln}"
        for f, hits in found.items() if f.startswith(STRICT)
        for ln in hits
    ]
    assert offenders == [], (
        "A caught error here does nothing and says nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither handle it, or write one line saying why not — a "
          "comment inside the handler is all this guard asks for."
    )


def test_the_backend_debt_only_ever_shrinks(found):
    counts: dict[str, int] = {}
    for f, hits in found.items():
        if f.startswith(STRICT) or f in EXEMPT:
            continue
        counts[_area(f)] = counts.get(_area(f), 0) + len(hits)
    grew = {a: (n, BUDGET.get(a, 0)) for a, n in counts.items() if n > BUDGET.get(a, 0)}
    assert not grew, (
        "New swallowed errors:\n  "
        + "\n  ".join(f"{a}: {now} now, {was} budgeted" for a, (now, was) in grew.items())
        + "\n\nWrite one line inside the handler saying why the error is "
          "dropped. If you meant to pay debt down instead, lower the "
          "number in BUDGET to match."
    )


def test_a_budget_that_is_too_generous_is_a_stale_budget(found):
    """The half most ratchets forget.

    A budget nobody lowers after fixing something stops being a ratchet
    and becomes a floor — it silently re-permits what was just paid off.
    Today's stale entry is tomorrow's re-introduced bug, and this is the
    same check that caught a dead exemption in the dashboard's chrome
    guard on the day this file was written.
    """
    counts: dict[str, int] = {}
    for f, hits in found.items():
        if f.startswith(STRICT) or f in EXEMPT:
            continue
        counts[_area(f)] = counts.get(_area(f), 0) + len(hits)
    stale = {a: (counts.get(a, 0), b) for a, b in BUDGET.items() if counts.get(a, 0) < b}
    assert not stale, (
        "These budgets are higher than reality — lower them, the debt was paid:\n  "
        + "\n  ".join(f"{a}: {now} actual, {was} budgeted" for a, (now, was) in stale.items())
    )


def test_every_class_exemption_still_exempts_something(found):
    """An exemption that exempts nothing is a rule nobody is watching."""
    dead = [f for f in EXEMPT if f not in found]
    assert dead == [], (
        f"These files no longer swallow anything — drop them from EXEMPT: {dead}"
    )


def test_the_scanner_cannot_be_fooled_by_prose():
    """It reads CODE, not the sentences describing it.

    A guard that matches its own explanation fails on the day somebody
    documents the bug it exists for — which happened twice in this repo
    before this line was written.
    """
    from tests._swallow import scan_ts, scan_py
    assert scan_ts("// see the old .catch(() => {}) here\nfoo();") == []
    assert scan_ts("const s = '} catch { }';") == []
    assert scan_ts("try { a(); } catch { }") == [1]
    assert scan_ts("try { a(); } catch { /* why */ }") == []
    assert scan_ts("p().catch(() => {});") == [1]
    assert scan_py("try:\n    a()\nexcept Exception:\n    pass\n") == [3]
    assert scan_py("try:\n    a()\nexcept Exception:\n    # why\n    pass\n") == []
