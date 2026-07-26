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
docs/architecture/alert-dm-migration.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Module prefixes each side is forbidden to import.
FORBIDDEN = {
    "features": ("capabilities.platform",),
    "capabilities/platform": ("features",),
    # The delivery spine stays source-blind: sources register INTO it.
    "capabilities/notifications": ("capabilities.alerting", "features"),
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
    for path in sorted((REPO / root).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(REPO))
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
