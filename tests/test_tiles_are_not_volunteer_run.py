"""No shipped surface draws from a volunteer-run tile server.

On 2026-09-11 OpenStreetMap blocked this product.  The owner saw it in
the browser panel — a fleet map filled with tiles that are not maps but
PICTURES reading "403 Access blocked".

Their usage policy forbids two things this product was doing:

* distributing an app that draws from openstreetmap.org — the Chrome
  panel, the dashboard and the Telegram mini app all did;
* bulk downloading — and this is the likelier cause.  The server RENDERS
  800x600 static maps for reports and parking by pulling a dozen or more
  tiles per image, from one IP, with a library's default User-Agent.

The panel was fixed first because it was the surface in front of the
owner.  It was not the worst offender, and fixing only what is visible
is how the block would have been earned again.

OpenTopoMap is in this guard for the same reason it was in the move: it
is the same kind of service under the same kind of policy, and keeping a
second volunteer server after the first one blocks you is making the
mistake twice.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

BLOCKED_HOSTS = ("tile.openstreetmap.org", "tile.opentopomap.org")

#: Every tree that ships. `_archive` holds built zips of past versions and
#: is history, not code.
SHIPPED = (
    "interfaces/browser_extension/src",
    "interfaces/dashboard/src",
    "interfaces/system_dashboard/src",
    "interfaces/miniapp/src",
    "interfaces/miniapp/js",
    "capabilities",
    "features",
    "adapters",
    "constants.py",
)

SUFFIXES = (".ts", ".tsx", ".js", ".py", ".html")


def _visible(path) -> str:
    """The file with its comments stripped.

    The move left comments naming the blocked hosts so the next reader
    learns why — and a guard that reads prose would match its own
    explanation, which is how a rule comes to forbid describing itself.
    """
    src = path.read_text(encoding="utf-8", errors="ignore")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", " ", src, flags=re.M)
    src = re.sub(r"^\s*#.*$", " ", src, flags=re.M)
    return src


def _files():
    for rel in SHIPPED:
        p = REPO / rel
        if p.is_file():
            yield p
            continue
        for f in p.rglob("*"):
            if (f.suffix in SUFFIXES and f.is_file()
                    and "node_modules" not in f.parts and "dist" not in f.parts):
                yield f


@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_no_shipped_file_draws_from(host: str) -> None:
    hits = [str(f.relative_to(REPO)) for f in _files() if host in _visible(f)]
    assert not hits, (
        f"{host} is volunteer-run and blocked this product once. "
        f"Still referenced by: {hits}"
    )


def test_the_sweep_actually_reaches_the_map_code() -> None:
    """A path list that had gone stale would pass this file in silence."""
    seen = {str(f.relative_to(REPO)) for f in _files()}
    for must in ("interfaces/browser_extension/src/features/live-map/tiles.ts",
                 "interfaces/dashboard/src/hooks/useLeafletMap.ts",
                 "interfaces/miniapp/src/pages/MapPage.tsx",
                 "constants.py"):
        assert must in seen, f"the sweep no longer reaches {must}"
