"""Two seams the POI split created, and the two ways they break quietly.

`pois.py` was one 1137-line module: every helper and every route shared
one namespace, so neither of these could happen.  Splitting it into
``features/live_map/poi/`` bought readability and introduced both traps
inside one afternoon.

1. A LOCAL THAT HIDES A MODULE.  ``patch_custom_layer`` had a local
   called ``overpass`` holding a query string.  Harmless while the name
   meant nothing else; the moment the file reached the Overpass client
   through a module of that name, ``overpass._validate_overpass_query``
   became an attribute lookup on a ``str``.  Python says nothing until
   the line runs — and it runs only when someone edits a layer's query.

2. A NAME BOUND AT IMPORT TIME CANNOT BE PATCHED.  Three tests patch
   ``features.live_map.poi.overpass._get_http_session`` so the pin-drop
   route talks to a fake.  A ``from .overpass import _get_http_session``
   in the calling module would bind the real function at import, the
   patch would sail past it, and the test would go to the real network
   and still pass — the same "failure dressed as a successful answer"
   shape that hid the tile block and the missing Overpass area.
"""
from __future__ import annotations

import ast

from tests._repo import REPO

PKG = REPO / "features" / "live_map" / "poi"

#: Everything here reaches the network.  Outside overpass.py they are
#: called as ``overpass.<name>(...)`` so a test patching the module
#: attribute is actually in the call path.
NETWORK_ENTRY_POINTS = {
    "_get_http_session",
    "_fetch_overpass",
    "_overpass_post",
    "_count_brand_in_usa",
    "_sample_brand_in_usa",
}


def _modules() -> dict[str, ast.Module]:
    return {p.name: ast.parse(p.read_text(encoding="utf-8"))
            for p in sorted(PKG.glob("*.py"))}


def _imported_module_names(tree: ast.Module) -> set[str]:
    """Names bound to a MODULE at the top of the file."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level and not node.module:
            # `from . import overpass, viewport`
            names.update(a.asname or a.name for a in node.names)
    return names


def test_no_local_hides_an_imported_module():
    for filename, tree in _modules().items():
        modules = _imported_module_names(tree)
        if not modules:
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = {a.arg for a in func.args.args + func.args.kwonlyargs}
            for node in ast.walk(func):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    bound.add(node.id)
            clash = bound & modules
            assert not clash, (
                f"{filename}:{func.name} binds {sorted(clash)}, which is also a "
                f"module this file imports — every `{sorted(clash)[0]}.x` after "
                f"that line is an attribute lookup on the local, and Python "
                f"only says so when the line runs")


def test_the_network_is_reached_through_the_module_not_by_name():
    for filename, tree in _modules().items():
        if filename == "overpass.py":
            continue          # its own names, its own namespace
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "overpass":
                continue
            imported = {a.name for a in node.names}
            leaked = imported & NETWORK_ENTRY_POINTS
            assert not leaked, (
                f"{filename} imports {sorted(leaked)} by name — a test that "
                f"patches features.live_map.poi.overpass.{sorted(leaked)[0]} "
                f"would not be in this call path, and the 'mocked' request "
                f"would go to the real Overpass mirror while the test passed")


def test_only_the_router_may_import_the_api_layer():
    """docs/FEATURES.md's dependency exception, checked where it now has
    five files to stay true across."""
    for filename, tree in _modules().items():
        reaches_api = any(
            isinstance(n, ast.ImportFrom)
            and (n.module or "").startswith("interfaces.api")
            for n in ast.walk(tree)
        )
        if filename == "router.py":
            assert reaches_api, "router.py stopped being the HTTP surface"
        else:
            assert not reaches_api, (
                f"{filename} imports interfaces.api — only router.py may "
                f"(docs/FEATURES.md, 'Dependency exception, stated once')")
