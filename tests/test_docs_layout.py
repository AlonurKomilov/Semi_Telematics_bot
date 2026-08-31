"""Documentation that future developers can trust, guarded.

The 2026-08-31 docs audit found 12 of 31 tracked docs carrying
references to files that no longer exist — devs changed the code and
the far-away docs never followed.  The migration moved each law next
to the code it binds (freshness beats filing: the owner's rule, and
the same mechanism that had 206 of 277 test files silently skipping
when they lived far from their code).  These three rules keep the
migrated world honest:

  1. A repo path named in a tracked doc must EXIST.  A doc pointing at
     a dead file reads authoritative and lies — the most dangerous
     staleness, because a future developer follows it.
  2. Every in-package law is INDEXED at docs/architecture/README.md.
     Scattering the laws must not scatter the census; the index is how
     anyone still answers "what systems have laws?" in one look.
  3. .dockerignore carries `**/docs/` — the tests/ anchoring trap,
     pre-paid: a bare root exclusion ships every package doc in the
     production image.
"""

from __future__ import annotations

import re
import subprocess

from tests._repo import REPO, scanned

# Deliberate placeholders, not dead paths: `features/x/...` is the
# template-speak the AI docs use for "your feature here", exactly as
# tests/test_x.py once was in the test docs.
_PLACEHOLDER = re.compile(r"(^|/)(x|<[^>]+>|\*+)(/|\.|$)")

_REF = re.compile(
    r"`([A-Za-z_][\w/.-]+\.(?:py|ts|tsx|md|ini|yml|json|sh))`")


def _tracked_docs() -> list:
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        capture_output=True, text=True, cwd=REPO,
    ).stdout.split()
    keep = []
    for f in out:
        parts = f.split("/")
        if "node_modules" in parts:
            continue
        if f.startswith("docs/") or "docs" in parts[:-1]:
            keep.append(f)
    return keep


def test_no_tracked_doc_references_a_dead_file():
    offenders: list[str] = []
    for f in scanned(_tracked_docs(), "tracked docs"):
        # Archived records describe the world AS IT WAS — a completed
        # rollout's references die with the migration it performed, and
        # rewriting them would falsify the record.  Living docs only.
        if "/archive/" in f:
            continue
        text = (REPO / f).read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(_REF.findall(text))):
            if "/" not in ref or _PLACEHOLDER.search(ref):
                continue
            if not (REPO / ref).exists():
                offenders.append(f"{f}: `{ref}`")
    assert not offenders, (
        "docs naming files that no longer exist — fix the reference or "
        "the doc is lying to the next developer:\n  "
        + "\n  ".join(offenders))


def test_every_in_package_law_is_indexed():
    index = (REPO / "docs/architecture/README.md").read_text(encoding="utf-8")
    missing = []
    for f in _tracked_docs():
        if not f.startswith("docs/") and "/docs/" in f:
            # any law directly under a package docs/ — runbooks and
            # archives are shelves, not laws, and stay un-indexed.
            tail = f.split("/docs/", 1)[1]
            if "/" in tail:
                continue
            if f not in index:
                missing.append(f)
    assert not missing, (
        "laws that moved in-package but never joined the index — the "
        "census must survive the scattering:\n  " + "\n  ".join(missing))


def test_package_docs_do_not_ship_in_the_image():
    patterns = [
        ln.strip() for ln in
        (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert "**/docs/" in patterns, (
        "`**/docs/` missing from .dockerignore — package docs would ship "
        "in the production image (the tests/ anchoring trap)."
    )
