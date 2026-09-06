"""The backend registry and the dashboard agree on what a feature is.

Three artifacts describe the same features: the backend registry
(capabilities/permissions/registry.py — billing's and the masks' unit),
the dashboard catalog (featureCatalog.ts — routes, nav, i18n) and the
matrix rows (permRows.ts — the tree an owner ticks).  Each has its own
job; none may say something the others contradict.  Cross-layer, so it
lives in the root suite.

The TypeScript is read structurally — balanced ``{ … }`` objects inside
the arrays that hold them — not line by line, so an entry a formatter
wraps over several lines is still seen; and every object that carries
an id or a key must be parsed, so a dropped one fails loud.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.registry import (
    CROSS_FEATURE_FLAGS, REGISTRY, TOGGLEABLE_MODULES, owner_of,
)
from tests._repo import REPO

_CATALOG = os.path.join(REPO, "interfaces/dashboard/src/config/featureCatalog.ts")
_ROWS = os.path.join(REPO, "interfaces/dashboard/src/features/permissions/permRows.ts")


def _strip_comments(src: str) -> str:
    """The source without ``//`` and ``/* */`` comments (strings kept
    whole).  A comment's apostrophe — "isn't" — would otherwise open a
    string for the bracket scanner and swallow the next object."""
    out, i, quote = [], 0, None
    while i < len(src):
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(src):
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = len(src) if j == -1 else j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = len(src) if j == -1 else j + 2
            continue
        else:
            if ch in ("'", '"', "`"):
                quote = ch
            out.append(ch)
        i += 1
    return "".join(out)


def _balanced(src: str, start: int, open_ch: str, close_ch: str) -> str:
    """The text from ``src[start]`` (an ``open_ch``) to its matching
    close, string literals skipped."""
    assert src[start] == open_ch, src[start:start + 20]
    depth, i, quote = 0, start, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unbalanced brackets")


def _objects(array_src: str) -> list[str]:
    """The top-level ``{ … }`` objects of an array literal, in order,
    each collapsed to one whitespace-normalised line."""
    out, i, quote = [], 0, None
    while i < len(array_src):
        ch = array_src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            obj = _balanced(array_src, i, "{", "}")
            out.append(re.sub(r"\s+", " ", obj))
            i += len(obj)
            continue
        i += 1
    return out


def _catalog(src: str | None = None) -> dict[str, dict]:
    src = _strip_comments(open(_CATALOG, encoding="utf-8").read() if src is None else src)
    consts: dict[str, list[str]] = {}
    for m in re.finditer(r"^const (P_[A-Z_]+) = (\[[^\]]*\]|'[^']*');", src, re.M):
        v = m.group(2)
        consts[m.group(1)] = re.findall(r"'([^']+)'", v) if v.startswith("[") else [v.strip("'")]
    anchor = src.index("export const FEATURE_CATALOG")
    # the array is the one after the ``=`` — the type annotation's
    # ``CatalogFeature[]`` carries an earlier, empty pair of brackets
    array = _balanced(src, src.index("[", src.index("=", anchor)), "[", "]")
    out: dict[str, dict] = {}
    for obj in _objects(array):
        idm = re.search(r"\bid: '([a-z_.-]+)'", obj)
        if not idm:
            continue
        mods = re.search(r"modules: \[([^\]]*)\]", obj)
        kind = re.search(r"kind: '([a-z]+)'", obj)
        perm = re.search(r"permission: (\[[^\]]*\]|'[^']*'|null|P_[A-Z_]+)", obj)
        flags: list[str] = []
        if perm and perm.group(1) != "null":
            pv = perm.group(1)
            flags = consts[pv] if pv.startswith("P_") else re.findall(r"'([^']+)'", pv) if pv.startswith("[") else [pv.strip("'")]
        out[idm.group(1)] = {
            "modules": frozenset(re.findall(r"'([^']+)'", mods.group(1))) if mods else frozenset(),
            "kind": kind.group(1) if kind else "feature",
            "opens": frozenset(flags),
        }
    # every id-bearing object was parsed — a dropped entry fails loud
    assert out, "the catalog array was not found"
    assert len(out) == len(re.findall(r"\bid: '", array)), "a catalog entry the parser could not read"
    return out


def _matrix_tree(src: str | None = None) -> list[tuple[str, str]]:
    """(parent key, child key) for every indented row under a row, in
    the staff matrix (the Driver panel is a flat list)."""
    src = open(_ROWS, encoding="utf-8").read() if src is None else src
    src = _strip_comments(src[: src.index("// ── Driver — self-service")])
    pairs, seen = [], 0
    for m in re.finditer(r"flags: \[", src):
        array = _balanced(src, m.end() - 1, "[", "]")
        parent = None
        for obj in _objects(array):
            if re.search(r"\bheader: '", obj):
                parent = None            # a header is a caption, not a row
                continue
            k = re.search(r"\b(?:key|allKey): '([^']+)'", obj)
            if not k:
                continue
            seen += 1
            if "indented: true" in obj:
                pk = re.search(r"parentKey: '([^']+)'", obj)
                if pk:
                    pairs.append((pk.group(1), k.group(1)))
                elif parent:
                    pairs.append((parent, k.group(1)))
            else:
                parent = k.group(1)
    # every row was visited — a row the parser could not read fails loud
    assert seen == len(re.findall(r"\b(?:key|allKey): '", src)), "a matrix row the parser could not read"
    return pairs


def test_the_parsers_read_a_wrapped_entry():
    # The guard's own blind spot: a formatter that wraps an object over
    # several lines.  Both parsers must still see it, whole.
    cat = _catalog("const P_X = ['can_view_vehicles'];\n"
                   "export const FEATURE_CATALOG = [\n"
                   "  {\n    id: 'wrapped',\n    modules: ['fleet',\n      'dispatch'],\n"
                   "    kind: 'feature', permission: P_X,\n    description: 'a {brace} in text',\n  },\n"
                   "  // a comment that isn't a string, with a { brace\n"
                   "  { id: 'flat', modules: ['core'], permission: null },\n];\n")
    assert cat["wrapped"] == {"modules": frozenset({"fleet", "dispatch"}), "kind": "feature",
                              "opens": frozenset({"can_view_vehicles"})}
    assert cat["flat"]["opens"] == frozenset()
    tree = _matrix_tree("const G = [{ title: 'x', flags: [\n"
                        "  {\n    key: 'can_view_maintenance', kind: 'feature',\n  },\n"
                        "  { key: 'can_manage_maintenance', kind: 'action', indented: true },\n"
                        "  { header: 'Costs' },\n"
                        "  { key: 'can_view_fuel_cost', kind: 'component', indented: true },\n"
                        "]}];\n// ── Driver — self-service\n")
    assert tree == [("can_view_maintenance", "can_manage_maintenance")]


def test_every_catalog_entry_is_in_the_registry_and_agrees():
    cat = _catalog()
    modules = set(TOGGLEABLE_MODULES)
    for cid, c in cat.items():
        if cid in modules:
            continue                 # the five department pseudo-entries
        assert cid in REGISTRY, f"catalog id {cid!r} has no registry entry"
        e = REGISTRY[cid]
        assert e.nav, f"{cid}: registry says no nav entry, the catalog has one"
        assert e.kind == c["kind"], (cid, e.kind, c["kind"])
        assert e.modules == c["modules"], (cid, sorted(e.modules), sorted(c["modules"]))
        assert set(e.opens) == c["opens"], (cid, e.opens, sorted(c["opens"]))


def test_every_nav_registry_entry_is_in_the_catalog():
    cat = _catalog()
    for e in REGISTRY.values():
        if e.nav:
            assert e.id in cat, f"registry {e.id!r} has nav=True but no catalog entry"
        else:
            assert e.id not in cat, f"registry {e.id!r} says nav=False but the catalog has it"


def test_the_matrix_tree_nests_flags_under_their_own_feature():
    pairs = _matrix_tree()
    assert len(pairs) > 15, "the matrix has nested rows; none were seen"
    for parent_key, child_key in pairs:
        if child_key in CROSS_FEATURE_FLAGS:
            continue
        p, c = owner_of(parent_key), owner_of(child_key)
        assert p is not None and c is not None, (parent_key, child_key)
        assert c is p or c.parent == p.id, (
            f"matrix nests {child_key} under {parent_key}, but the registry "
            f"puts them in {c.id!r} and {p.id!r}")
