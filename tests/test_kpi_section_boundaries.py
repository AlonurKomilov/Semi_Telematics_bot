"""KPI sections are ISOLATED — the structural guarantee behind the
per-role split.

The owner's requirement, verbatim: a dispatch change must never take
effect on another role's KPI.  The architecture that delivers it:

  * ``features/kpi/`` root  — orchestration + shared pieces only.
    The root MAY import sections (aggregating their routers and
    re-exporting section config for the root endpoints IS its job).
  * ``features/kpi/<section>/`` — one package per section (dispatch
    today; fleet / safety / hr / drivers as they land — every section
    grades EMPLOYEES of that role, never assets), each owning its
    engine, endpoints and config keys.
  * A section's SUBJECT is that role's EMPLOYEES (owner fundament:
    KPI grades people, never assets — a fleet manager's grade may be
    computed THROUGH asset numbers, but the row is the person).
  * A section may import the ROOT's shared pieces and ITSELF —
    **never a sibling section.**  The moment ``kpi/fleet`` imports
    ``kpi.dispatch.engine``, a dispatch refactor can break fleet
    grading; this test makes that a red build instead of a production
    surprise.

Written generically: a new section directory is covered the day it is
created, with no edit here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted

KPI_ROOT = _REPO / "features" / "kpi"


def _sections() -> list[str]:
    return sorted(
        p.name for p in KPI_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    )


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_kpi_sections_never_import_each_other():
    sections = _sections()
    assert "dispatch" in sections, "the dispatch section should exist"
    violations: list[str] = []
    for section in sections:
        others = [s for s in sections if s != section]
        for py in (KPI_ROOT / section).rglob("*.py"):
            for mod in _imported_modules(py):
                for other in others:
                    if mod == f"features.kpi.{other}" or mod.startswith(
                        f"features.kpi.{other}."
                    ):
                        violations.append(
                            f"{py.relative_to(KPI_ROOT.parent.parent)} "
                            f"imports {mod}"
                        )
    assert violations == [], (
        "KPI sections must stay isolated (a change in one role's KPI must "
        "not reach another's):\n  " + "\n  ".join(violations)
    )


def test_kpi_section_modules_do_not_share_settings_keys():
    """Each section owns its config storage keys.  Two sections writing
    one ``account_settings`` key is the same coupling as an import —
    invisible until one section's save clobbers the other's config."""
    sections = _sections()
    keys: dict[str, str] = {}
    clashes: list[str] = []
    for section in sections:
        for py in (KPI_ROOT / section).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name)
                                and t.id.endswith("_SETTING_KEY")
                                for t in node.targets)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    key = node.value.value
                    owner = keys.setdefault(key, section)
                    if owner != section:
                        clashes.append(
                            f"settings key {key!r} declared by both "
                            f"{owner} and {section}")
    assert clashes == [], "\n".join(clashes)
