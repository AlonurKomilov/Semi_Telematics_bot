"""Layer-boundary guard — the audience split is CI-enforced, not remembered.

``capabilities/platform/`` holds SYSTEM-OWNER domains (billing, …): things
that serve 4truck the operator, not the customer's daily work.  The customer
product (``features/``) and platform domains must never depend on each other:

  * ``features/**``              must NOT import ``capabilities.platform.*``
  * ``capabilities/platform/**`` must NOT import ``features.*``

Deliberate seam that stays legal: tenant-serving capabilities MAY call INTO
platform (e.g. the Samsara ingest cycle triggers billing quantity sync) —
capabilities→platform is a one-way service call, not a product dependency.

Rationale + the "Whose money is it?" table: docs/FEATURES.md "Money domains".

A second guarded seam: ``capabilities/notifications/`` is the GENERIC
delivery spine (channels, inbox, prefs, digests).  Event sources
(alerting, billing, …) import notifications to register categories and
dispatch — never the reverse: the spine must stay source-blind, or the
next domain (work orders, loads) can't reuse it.  Sources hand the spine
closures (``audience``, ``recipient_filter``, renderers) instead of being
imported.  Decision + DM-migration plan:
capabilities/alerting/docs/alert-dm-migration.md.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted
from tests._repo import is_test_path, scanned  # a guard that scans nothing must fail

REPO = _REPO

# Module prefixes each side is forbidden to import.
FORBIDDEN = {
    "features": ("capabilities.platform",),
    "capabilities/platform": ("features",),
    # The delivery spine stays source-blind: sources register INTO it.
    "capabilities/notifications": ("capabilities.alerting", "features"),
    # THE WALL (alert-dm-migration end state): alerting detects, stores
    # and decides — it never touches the transport.  Every send, edit,
    # button and deferral goes through capabilities/notifications.
    "capabilities/alerting": ("telegram",),
    # Arc-end invariant (warehouse SSOT): integrations are
    # provider fetchers + shape adapters — feature logic reaches them
    # only through data_lifecycle registrations (e.g. the vehicle
    # cascade's reroll hook), never by import.
    "capabilities/integrations": ("features",),
}

# Narrow, deliberate exemptions (path prefixes, repo-relative).
# delivery_admin/ is the ALERT-routing config skin that re-homed under
# notifications (destination/channel config belongs to delivery) — it
# speaks alert vocabulary (subtype catalog, topic sentinel, persona→type
# mapping) by nature.  The spine CORE (channels/service/plan/…) stays
# absolutely walled; anything new that needs alerting belongs here or
# nowhere.
EXEMPT_PREFIXES = (
    "capabilities/notifications/delivery_admin/",
)


def _imports_of(path: Path) -> list[str]:
    """Every module name imported by *path* (import X / from X import Y)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # a broken file is some other test's problem
        return []
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.append(node.module)
    return mods


def _violations(root: str, banned: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for path in scanned(sorted((REPO / root).rglob("*.py")), f"{root} sources"):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(REPO))
        # Test code is not production code.  This wall had no exclusion
        # at all — harmless while every test lived in the top-level
        # tests/ tree, which this walk never entered.  Once a package
        # owns its own tests/, a test that imports across a boundary on
        # purpose (to assert the boundary) would be reported as breaking
        # it.
        if is_test_path(path.relative_to(REPO)):
            continue
        if any(rel.startswith(p) for p in EXEMPT_PREFIXES):
            continue
        for mod in _imports_of(path):
            for prefix in banned:
                if mod == prefix or mod.startswith(prefix + "."):
                    out.append(f"{path.relative_to(REPO)} imports {mod}")
    return out



@pytest.mark.parametrize(("root", "banned"), sorted(FORBIDDEN.items()))
def test_layer_boundary(root: str, banned: tuple[str, ...]):
    """Each side of the audience split stays import-clean.

    Parametrized over FORBIDDEN so the table IS the enforcement — adding a
    new rule pair there is automatically executed, never decorative.
    """
    bad = _violations(root, banned)
    assert not bad, (
        f"{root}/ must not import {', '.join(banned)}.* "
        "(audience boundary — see docs/FEATURES.md 'Money domains'):\n  "
        + "\n  ".join(bad)
    )


def test_platform_subfamily_exists_and_holds_billing():
    """Structure self-check — the sub-family is real, billing lives in it,
    and no stray top-level ``capabilities/billing`` reappears."""
    assert (REPO / "capabilities/platform/__init__.py").is_file()
    assert (REPO / "capabilities/platform/billing/router.py").is_file()
    assert not (REPO / "capabilities/billing").exists(), (
        "capabilities/billing/ reappeared at top level — platform domains "
        "live under capabilities/platform/ (docs/FEATURES.md)"
    )


def test_physical_warehouse_tables_stay_inside_the_machinery():
    """Physical warehouse table names (vehicle_state, vehicle_state_snapshot,
    vehicle_telemetry) are the ENGINE ROOM — SQL against them may exist only
    in the storage layer, the warehouse machinery, migrations, and operator
    scripts.  Everyone else reads the grain surfaces (vehicle_state_live/
    minute/hour/day/week, vehicle_health_*, vehicle_timeline — migration 185)
    so the physical layer stays free to change without moving a consumer.
    """
    import re

    allowed = (
        "adapters/storage/warehouse",
        "adapters/storage/migrations.py",
        "adapters/storage/vehicle_departure.py",
        "features/vehicles/warehouse/",
        "capabilities/data_lifecycle/",   # engines + the status router

        "capabilities/integrations/",     # ingest writers
        "scripts/",
    )
    # Test code is allowed to address the physical tables — 11 files do,
    # and they are legal ONLY by being test code.  That used to be
    # spelled as the literal prefix "tests/" in the tuple above, which
    # stops being true the moment a package owns its own tests/ dir:
    # every one of those files would fail the build with "write outside
    # machinery". is_test_path() answers it by directory, wherever it is.
    # READ verbs may address the grain tables (they took the surface
    # names — that is the public read API); WRITE verbs on them stay
    # machinery-only; and the RETIRED names (vehicle_state bare,
    # vehicle_state_snapshot, vehicle_telemetry) may appear nowhere —
    # a straggler means unswept code.
    write_verb = re.compile(
        r"(INTO|UPDATE|DELETE\s+FROM)\s+"
        r"(warehouse\.)?vehicle_state_(live|minute|hour|day|week)\b"
    )
    # Retired names are checked EVERYWHERE except the historical
    # migration bodies — the allowlist must not shelter stragglers
    # (a scripts/ file once hid one exactly this way).
    retired = re.compile(
        r"(FROM|INTO|UPDATE|JOIN)\s+"
        r"(warehouse\.)?(vehicle_(state_snapshot|telemetry|health_snapshot|fault_snapshot|fault_detail)|weather_snapshot|efficiency_snapshot)\b"
        r"|(FROM|INTO|UPDATE|JOIN)\s+(warehouse\.)?vehicle_state\b(?!_)"
    )
    offenders = []
    for path in scanned(REPO.rglob("*.py"), "repo sources"):
        rel = path.relative_to(REPO).as_posix()
        if "__pycache__" in rel:
            continue
        txt = path.read_text(errors="ignore")
        if rel != "adapters/storage/migrations.py" and retired.search(txt):
            offenders.append(rel + "  (retired table name)")
        if (not rel.startswith(allowed) and not is_test_path(rel)
                and write_verb.search(txt)):
            offenders.append(rel + "  (write outside machinery)")
    assert not offenders, (
        "SQL against PHYSICAL warehouse tables outside the machinery — "
        "read through the grain surfaces instead:\n  " + "\n  ".join(offenders)
    )


# ── Every raw read of `vehicles` declares its stance on retired rows ──
#
# `is_active = 0` means the truck left the fleet. Most queries want it
# gone; some genuinely need it — a work order's history, a collision
# check against a unique index that spans retired rows, the identity
# watch that must keep anchoring a truck whose gateway might move.
#
# The rule is not "always filter". It is "say which". An unmarked query
# is one nobody has thought about, and that is how a customer ended up
# being alerted, invoiced and assigned inspections for trucks they had
# retired weeks earlier.

_VEHICLES_READ = re.compile(r"FROM\s+vehicles\b", re.I)
_HAS_STANCE = re.compile(r"is_active|archived_reason", re.I)
# Punctuation-agnostic on purpose: `archived-ok:` and
# `archived-ok, because …` are the same declaration, and a guard
# that rejects one of them teaches people to fight the guard.
_MARKER = re.compile(r"#\s*archived-ok\b")

#: How far above the SQL a marker may sit. Generous enough for a real
#: explanation, short enough that it must belong to THIS query.
_MARKER_LOOKBACK = 12


def test_every_raw_vehicles_read_declares_its_stance_on_retired_rows():
    exempt_dirs = ("/tests/", "/scripts/", "/node_modules/", "/__pycache__/")
    exempt_files = {
        # Schema and migrations define the table; they precede the rule.
        "migrations.py", "schema.py",
        "platform_schema.py", "platform_migrations.py",
    }
    offenders: list[str] = []
    scanned_files = 0
    for path in REPO.rglob("*.py"):
        rel = str(path.relative_to(REPO))
        if any(d in f"/{rel}" for d in exempt_dirs):
            continue
        if path.name in exempt_files or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not _VEHICLES_READ.search(text):
            continue
        scanned_files += 1
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not _VEHICLES_READ.search(line):
                continue
            # The statement is an implicit string concat over several
            # lines; look at the whole neighbourhood, not one line.
            window = "\n".join(lines[max(0, i - _MARKER_LOOKBACK): i + 6])
            if _HAS_STANCE.search(window) or _MARKER.search(window):
                continue
            offenders.append(f"{rel}:{i + 1}")

    assert scanned_files, "no file reads `vehicles` — the scan is broken"
    assert not offenders, (
        "raw `FROM vehicles` with no stance on retired rows:\n    "
        + "\n    ".join(offenders)
        + "\n\nEither filter (`is_active = 1`, or `archived_reason` when the "
          "sweep-vs-operator distinction matters), or write "
          "`# archived-ok: <why this one must see retired trucks>` above it."
    )
