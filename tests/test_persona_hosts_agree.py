"""The persona → host map exists in three places.  They must agree.

The app is served from per-persona subdomains — `dash.`, `fleet.`,
`dispatch.`, `safety.`, `hr.`, `accounting.`, `recruiter.` — all
carrying the SAME bundle, with the hostname telling the dashboard which
shell to open.  docs/architecture/PERSONA.md names
`RoleViewContext.tsx` as the place to edit when a persona is added.

Three copies, each for its own flow and each deliberate:

  * `RoleViewContext.ROLE_HOST`  — the persona selector's navigation.
  * `AuthContext.ROLE_TO_HOST`   — the redirect after sign-in.
  * `appHost.ROLE_SUBDOMAIN`     — the browser extension's deep links,
                                   across a package boundary.

The extension one was added because the panel had been building deep
links on the APEX, which nginx answers 404 for every path but the
sign-in pages: the overlay card's "Web" button opened
`4truck.us/vehicles/001?company=PTG` and the owner found a 404 page.
Two Inventory doors had the same bug and had never been pressed.

A fourth place to check is nginx itself — a host in the maps that no
server block answers is a 404 with extra steps.
"""
from __future__ import annotations

import re

from tests._repo import REPO

DASH = REPO / "interfaces" / "dashboard" / "src" / "context"
EXT = REPO / "interfaces" / "browser_extension" / "src"
NGINX = REPO / "nginx" / "4truck.conf"


def _ts_map(path, name: str) -> dict[str, str]:
    """Read a `const NAME: Record<string, string> = { … }` literal."""
    src = path.read_text(encoding="utf-8")
    m = re.search(rf"const {name}: Record<string, string> = \{{(.*?)\n\}};", src, re.S)
    assert m, f"{name} not found in {path.name}"
    out: dict[str, str] = {}
    for role, value in re.findall(r"(\w+):\s*[`'\"]([^`'\"]*)[`'\"]", m.group(1)):
        # `fleet.${APEX_DOMAIN}` → "fleet"; a bare label stays itself.
        out[role] = value.split(".")[0].strip()
    return out


def test_the_dashboard_two_copies_agree_with_each_other():
    a = _ts_map(DASH / "RoleViewContext.tsx", "ROLE_HOST")
    b = _ts_map(DASH / "AuthContext.tsx", "ROLE_TO_HOST")
    assert a == b, (
        "the persona selector and the post-login redirect would send the "
        f"same person to different hosts: {a} vs {b}")


def test_the_extension_agrees_with_the_dashboard():
    dash = _ts_map(DASH / "RoleViewContext.tsx", "ROLE_HOST")
    ext = _ts_map(EXT / "appHost.ts", "ROLE_SUBDOMAIN")
    assert ext == dash, (
        "the panel's deep links would open a different host from the one "
        f"the dashboard sends the same person to: {ext} vs {dash}")


def test_every_host_in_the_map_is_actually_served():
    """A persona pointed at a subdomain nginx does not answer is a 404
    with extra steps — which is the whole bug this file is about."""
    conf = NGINX.read_text(encoding="utf-8")
    served = set()
    for line in re.findall(r"server_name\s+([^;]+);", conf):
        for host in line.split():
            served.add(host.strip().lower())
    missing = sorted({
        f"{label}.4truck.us"
        for label in _ts_map(DASH / "RoleViewContext.tsx", "ROLE_HOST").values()
    } - served)
    assert not missing, f"no nginx server_name answers: {missing}"


def test_the_apex_is_not_where_the_app_lives():
    """The reason the extension needed its own map at all.

    If this ever stops being true — the apex starts serving the SPA —
    the panel could go back to one base.  Until then, a deep link on
    the apex is a 404.
    """
    conf = NGINX.read_text(encoding="utf-8")
    apex = re.search(r"server_name 4truck\.us;(.*?)\n}", conf, re.S)
    assert apex, "the apex server block moved"
    body = apex.group(1)
    assert "location / {\n        return 404;" in body, (
        "the apex no longer 404s unknown paths — re-check whether the "
        "extension still needs appHost.ts")
