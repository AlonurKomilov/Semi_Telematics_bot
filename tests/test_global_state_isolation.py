"""Every process-global that a test can reach has been decided about.

A module-level dict/list/set outlives a test.  Combined with ``pg_db``
handing each test a database COPIED FROM A TEMPLATE — so account ids
restart at the same value every time — an entry written by one test is a
valid-looking hit for the next test's brand-new account.  Three bugs
came out of exactly that, and each read as flakiness first:

  * ``_permissions_cache`` still said can_faults=False, so a PUT
    produced an empty diff and the trail assertion died on KeyError.
  * two of registry.py's three advertised-tool caches were isolated and
    the third was not.
  * a ``FakeChannel`` registered by the digest tests outlived them and
    failed a notifications ROUTES test in another package.

Finding those cost a worktree baseline, several 15-minute runs and a
mutation probe each.  This guard exists so the next one costs nothing:
a new process-global cannot enter the tree without someone saying which
kind it is.

TWO WAYS TO SATISFY IT, and they mean different things:

  ISOLATED — named in conftest's ``_PROCESS_CACHES`` (cleared) or
  ``_GLOBAL_REGISTRIES`` (snapshot/restored).  Use this when a test can
  observe the value: anything keyed by account/user id, and any registry
  a test might register a double into.

  ANNOTATED — ``# test-safe: <why>`` on the definition line or the line
  above.  Use this when the value cannot cross a test boundary, or when
  clearing it would be wrong.  The reason is the point; a bare marker is
  rejected.

Deliberately NOT enforced: that every global is isolated.  Clearing a
registry that production populates at import would break the suite it is
meant to protect — see the note on registries in conftest.
"""

from __future__ import annotations

import ast
import io
import re

from tests._repo import REPO, is_test_path, scanned

_LAYERS = ("capabilities", "features", "adapters", "infra", "interfaces")

_MUTATORS = ("clear", "append", "update", "pop", "add", "setdefault",
             "discard", "remove", "insert", "extend")


def _mutated_globals() -> list[tuple[str, str, int, str]]:
    """(dotted_module, name, lineno, source_line) per reachable global."""
    found = []
    for layer in _LAYERS:
        base = REPO / layer
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            rel = p.relative_to(REPO)
            if "node_modules" in rel.parts or is_test_path(rel):
                continue
            try:
                src = io.open(p, encoding="utf-8", errors="ignore").read()
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            lines = src.splitlines()
            for node in tree.body:              # module level only
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, ast.AnnAssign)
                           else [])
                for tgt in targets:
                    if not (isinstance(tgt, ast.Name)
                            and isinstance(node.value, (ast.Dict, ast.List, ast.Set))):
                        continue
                    name = tgt.id
                    # __all__ is export metadata; a public UPPERCASE name is a
                    # declared constant.  A _PRIVATE uppercase name is exactly
                    # how the registries in this repo are spelled, so those
                    # stay in scope.
                    if name == "__all__" or (name.isupper() and not name.startswith("_")):
                        continue
                    mutates = re.search(
                        rf"\b{re.escape(name)}\s*\[[^\]]+\]\s*=|"
                        rf"\b{re.escape(name)}\.(?:{'|'.join(_MUTATORS)})\(", src)
                    if not mutates:
                        continue
                    dotted = ".".join(rel.with_suffix("").parts)
                    # The whole contiguous comment block above the
                    # definition, not just the line touching it: a reason
                    # worth reading usually needs more than one line, and a
                    # guard that only looked at the last one would push
                    # authors to write terser reasons than the thing
                    # deserves.
                    i = node.lineno - 2
                    while i >= 0 and lines[i].lstrip().startswith("#"):
                        i -= 1
                    block = lines[i + 1:node.lineno]
                    found.append((dotted, name, node.lineno, "\n".join(block)))
    return sorted(found)


def _isolated_pairs() -> set[tuple[str, str]]:
    import conftest
    return {(m, n) for m, n in
            (*conftest._PROCESS_CACHES, *conftest._GLOBAL_REGISTRIES)}


_ANNOTATION = re.compile(r"#\s*test-safe:\s*(?P<why>\S.*)")


def test_every_process_global_is_isolated_or_annotated():
    isolated = _isolated_pairs()
    undecided: list[str] = []
    unexplained: list[str] = []

    for dotted, name, lineno, context in scanned(
            _mutated_globals(), "process-global declarations"):
        if (dotted, name) in isolated:
            continue
        m = _ANNOTATION.search(context)
        if not m:
            undecided.append(f"{dotted.replace('.', '/')}.py:{lineno}: {name}")
        elif len(m.group("why").strip()) < 12:
            unexplained.append(f"{dotted.replace('.', '/')}.py:{lineno}: {name}")

    assert not undecided, (
        f"{len(undecided)} process-global(s) neither isolated nor annotated. "
        "Each outlives a test, and every test gets a database copied from a "
        "template — so account ids repeat and a stale entry reads as a hit.\n"
        "  Add the (module, name) pair to conftest's _PROCESS_CACHES "
        "or _GLOBAL_REGISTRIES, or write `# test-safe: <why it cannot cross "
        "a test boundary>` on its definition:\n    "
        + "\n    ".join(undecided))

    assert not unexplained, (
        "`# test-safe:` needs a REASON, not a marker — the next reader has "
        "to be able to check the claim:\n    " + "\n    ".join(unexplained))
