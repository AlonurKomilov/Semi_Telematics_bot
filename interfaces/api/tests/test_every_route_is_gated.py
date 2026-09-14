"""Every mounted route carries the gate its audience needs.

The attack surface is a ROUTE, not a package. A guard that asks "does
this package have a security test?" is satisfied by writing a test; this
one is satisfied only by gating the route — it reads the dependency tree
FastAPI actually built, so the thing it measures is the code.

Three questions, each with the half most ratchets forget — the entry
that outlives its reason:

1. **Is anything unauthenticated?** Everything public is listed here
   with why. A new route that forgets its dependency lands in this list
   as a failure, not as a quiet hole.
2. **Does every `/system/*` route require the system owner?** No
   allow-list, and none is needed: all 99 already do. This is the line
   that keeps the system layer a system layer — the directory move gave
   it a home, and this keeps a route from wandering out of it.
3. **Does every tenant route carry a permission gate**, rather than
   only proving somebody is logged in? The ones that do not are listed,
   and that list is the honest work queue: a hundred routes that any
   signed-in person of any role can reach.

What this guard does NOT prove: that the gate is the RIGHT one, or that
a handler past the gate filters rows by account. Those are behavioural,
and belong to the package that owns the query — cross-account leakage is
NOT structurally prevented here (row-level security is enabled on one
table of forty-one), so a package whose handler builds its own filter
still owes its own test.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "x" * 32)

import pytest

pytestmark = pytest.mark.security

#: Longest first: ``/api/v1`` must strip before ``/api``.
MOUNT_PREFIXES = ("/api/v1", "/api")

#: Proves the caller is somebody.
AUTHN = "get_current_user"

#: Proves the caller is US.
OWNER = "require_system_owner"

#: Proves the caller is a MACHINE of ours — a shared secret, compared in
#: constant time, with no session behind it. Exactly one route uses it
#: and it is named below: a finished pytest process reporting what it
#: did. It is a gate, so it is recognised; it is not the operator, so it
#: cannot satisfy the /system/* rule on its own.
MACHINE = "require_suite_token"

#: The ``/system/*`` routes held by a machine token instead of the
#: system owner. Listed one by one, with why, because every entry is a
#: door into the operator's half of the platform that a person does not
#: open.
SYSTEM_MACHINE_ROUTES: dict[str, str] = {
    "POST /system/suite/runs":
        "a finished pytest process posting what it did — it has no "
        "session and no operator to be",
}

#: Proves the caller is allowed THIS. Matched as a prefix because these
#: are factories: ``require_permission("loads")`` closes over the feature
#: and the dependency's qualname is ``require_permission.<locals>._check``.
PERMISSION_FACTORIES = (
    "require_permission",
    "require_permission_any",
    "require_wide",
    "require_any_or_wide",
)


def _strip_mount(path: str) -> str:
    for prefix in MOUNT_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _gates(route) -> set[str]:
    """Every dependency callable in the route's tree, by qualname.

    Recursive on purpose: ``require_system_owner`` itself depends on
    ``get_current_user``, and a route that declares only the outer one
    would look ungated to a single-level scan.
    """
    found: set[str] = set()

    def walk(dependant) -> None:
        for dep in getattr(dependant, "dependencies", []) or []:
            call = getattr(dep, "call", None)
            if call is not None:
                found.add(getattr(call, "__qualname__", getattr(call, "__name__", "")))
            walk(dep)

    walk(route.dependant)
    return found


def _classify(gates: set[str]) -> str:
    if any(g.startswith(OWNER) for g in gates):
        return "system_owner"
    if any(g.startswith(MACHINE) for g in gates):
        return "machine"
    if any(g.startswith(f) for g in gates for f in PERMISSION_FACTORIES):
        return "permission"
    if AUTHN in gates:
        return "auth_only"
    return "public"


def _routes():
    """Every mounted route as (methods, path, classification).

    Deduped across the two mounts — the app serves every router at both
    ``/api`` and ``/api/v1``, so a violation would otherwise be reported
    twice and a fix would have to be made in two list entries.
    """
    from fastapi.routing import APIRoute

    from interfaces.api.app import create_api

    app = create_api()
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        methods = tuple(sorted(r.methods - {"OPTIONS"}))
        if not methods:
            continue
        path = _strip_mount(r.path)
        if (path, methods) in seen:
            continue
        seen.add((path, methods))
        out.append((methods, path, _classify(_gates(r))))
    return out


def _key(methods, path) -> str:
    return f"{','.join(methods)} {path}"


@pytest.fixture(scope="module")
def routes():
    found = _routes()
    assert len(found) > 400, (
        f"only {len(found)} routes found — the app did not mount, and a "
        f"guard that scans nothing reports success"
    )
    return found


# ── 1. what is reachable without signing in ──────────────────────────
#
# Every entry is a deliberate hole with a reason. Shortest list in this
# file on purpose: it is the only place a real one can hide.
PUBLIC: dict[str, str] = {
    'GET /applications/brand':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'GET /applications/brand-banner':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'GET /applications/brand-logo':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'GET /applications/carrier-lookup':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'GET /auth/bot-login/check/{token}':
        "the sign-in surface — it cannot require what it grants",
    'GET /auth/config':
        "the sign-in surface — it cannot require what it grants",
    'GET /auth/invite-preview':
        "the sign-in surface — it cannot require what it grants",
    'GET /auth/invite/decline':
        "the sign-in surface — it cannot require what it grants",
    'GET /auth/system-config':
        "the sign-in surface — it cannot require what it grants",
    'GET /auth/verify-email':
        "the sign-in surface — it cannot require what it grants",
    'GET /carrier-directory/intake':
        "the public carrier intake — gated by its link TOKEN, not a JWT",
    'GET /dashboard':
        "the SPA shell itself — static files, no data",
    'GET /dashboard/':
        "the SPA shell itself — static files, no data",
    'GET /dashboard/{full_path:path}':
        "the SPA shell itself — static files, no data",
    'GET /health':
        "monitors, which have no account",
    'GET /health/deps':
        "monitors, which have no account",
    'GET /health/watch':
        "monitors, which have no account",
    'GET /metrics':
        "monitors, which have no account",
    'GET /miniapp/{full_path:path}':
        "the SPA shell itself — static files, no data",
    'GET /notifications/unsubscribe':
        "one-click unsubscribe from an email — the token is the link",
    'GET /notifications/verify':
        "one-click unsubscribe from an email — the token is the link",
    'GET /object-storage/google/callback':
        "the Google OAuth callback — verified by its state parameter",
    'GET /og-image.png':
        "the public marketing site",
    'GET,HEAD /':
        "the public marketing site",
    'GET,HEAD /about':
        "the public marketing site",
    'GET,HEAD /contact':
        "the public marketing site",
    'GET,HEAD /privacy':
        "the public marketing site",
    'GET,HEAD /robots.txt':
        "the public marketing site",
    'GET,HEAD /sitemap.xml':
        "the public marketing site",
    'GET,HEAD /terms':
        "the public marketing site",
    'HEAD /health':
        "monitors, which have no account",
    'HEAD /health/watch':
        "monitors, which have no account",
    'POST /applications/application-status':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/apply':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/draft':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/draft/resume':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/draft/send-link':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/ocr-cdl':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /applications/track-view':
        "the public recruiter link — gated by its link TOKEN, not a JWT",
    'POST /auth/bot-login/init':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/complete-setup':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/forgot-password':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/google':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/invite/decline':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/login':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/logout':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/refresh':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/register':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/register-account':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/register-google':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/resend-verification':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/reset-password':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/set-password':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/system-telegram-init':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/system-telegram-login':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/telegram':
        "the sign-in surface — it cannot require what it grants",
    'POST /auth/telegram-login':
        "the sign-in surface — it cannot require what it grants",
    'POST /billing/stripe/webhook':
        "a provider talking to US (signature-verified), not a caller",
    'POST /billing/webhook':
        "a provider talking to US (signature-verified), not a caller",
    'POST /carrier-directory/intake':
        "the public carrier intake — gated by its link TOKEN, not a JWT",
    'POST /notifications/unsubscribe':
        "one-click unsubscribe from an email — the token is the link",
    'POST /notifications/unsubscribe-confirmed':
        "one-click unsubscribe from an email — the token is the link",
    'POST /webhooks/resend':
        "a provider talking to US (signature-verified), not a caller",
}


# ── 3. what any signed-in person of any role can reach ───────────────
#
# Only ``get_current_user`` — the caller is somebody, but nothing asks
# whether they may do THIS. Some belong here for good (your own profile,
# your own preferences); most are unreviewed. The list only shrinks.
AUTH_ONLY: dict[str, str] = {
    'DELETE /admin/alert-routing/custom-topics/{topic_id}':
        "unreviewed",
    'DELETE /admin/alert-routing/persona-groups/{persona}':
        "unreviewed",
    'DELETE /admin/bot-instances/{persona}':
        "unreviewed",
    'DELETE /ai/conversations/{conversation_id}':
        "unreviewed",
    'DELETE /ai/history':
        "unreviewed",
    'DELETE /alerts/triggers/{trigger_id:int}':
        "unreviewed",
    'DELETE /page-layouts/{role}/{feature}':
        "unreviewed",
    'DELETE /user/google':
        "unreviewed",
    'DELETE /user/preferences/ui/{key}':
        "the caller's own row — the path names them, nobody else",
    'DELETE /user/sessions/{session_id}':
        "the caller's own row — the path names them, nobody else",
    'DELETE /user/telegram':
        "unreviewed",
    'DELETE /vendors/{vendor_id}/link-directory':
        "unreviewed",
    'GET /activity/{entity_type}/{entity_id}':
        "unreviewed",
    'GET /admin/alert-routing':
        "unreviewed",
    'GET /admin/alert-routing/custom-topics':
        "unreviewed",
    'GET /admin/alert-routing/persona-topics':
        "unreviewed",
    'GET /admin/bot-config':
        "unreviewed",
    'GET /admin/bot-instances':
        "unreviewed",
    'GET /ai/actions/{proposal_id}':
        "unreviewed",
    'GET /ai/actions/{proposal_id}/rows':
        "unreviewed",
    'GET /ai/conversations':
        "unreviewed",
    'GET /ai/conversations/{conversation_id}/messages':
        "unreviewed",
    'GET /ai/history':
        "unreviewed",
    'GET /ai/models':
        "unreviewed",
    'GET /ai/tier':
        "unreviewed",
    'GET /alerts/triggers':
        "unreviewed",
    'GET /alerts/triggers/fired':
        "unreviewed",
    'GET /alerts/triggers/metrics':
        "unreviewed",
    'GET /alerts/triggers/vehicles':
        "unreviewed",
    'GET /extension/download':
        "unreviewed",
    'GET /extension/info':
        "unreviewed",
    'GET /extension/me':
        "unreviewed",
    'GET /extension/vehicle-link':
        "unreviewed",
    'GET /kpi/dispatch/me':
        "unreviewed",
    'GET /maintenance/due-locations':
        "unreviewed",
    'GET /maintenance/history/{vehicle_name}':
        "unreviewed",
    'GET /maintenance/odometer/{vehicle_name}':
        "unreviewed",
    'GET /maintenance/tasks':
        "unreviewed",
    'GET /maintenance/tasks.csv':
        "unreviewed",
    'GET /maintenance/tasks/{task_id}':
        "unreviewed",
    'GET /maintenance/tasks/{task_id}/attachment':
        "unreviewed",
    'GET /maintenance/tasks/{task_id}/history':
        "unreviewed",
    'GET /maintenance/templates':
        "unreviewed",
    'GET /overview/stats':
        "unreviewed",
    'GET /page-layouts':
        "unreviewed",
    'GET /user/account/lifecycle':
        "unreviewed",
    'GET /user/me':
        "the caller's own row — the path names them, nobody else",
    'GET /user/me/activity':
        "the caller's own row — the path names them, nobody else",
    'GET /user/me/alerts':
        "the caller's own row — the path names them, nobody else",
    'GET /user/me/export':
        "the caller's own row — the path names them, nobody else",
    'GET /user/preferences/ui':
        "the caller's own row — the path names them, nobody else",
    'GET /user/preferences/ui/{key}':
        "the caller's own row — the path names them, nobody else",
    'GET /user/sessions':
        "the caller's own row — the path names them, nobody else",
    'GET /user/telegram/link/status/{token}':
        "unreviewed",
    'GET /vendors':
        "unreviewed",
    'GET /vendors/directory/browse':
        "unreviewed",
    'GET /vendors/directory/search':
        "unreviewed",
    'GET /vendors/directory/{entry_id}/market':
        "unreviewed",
    'GET /vendors/identity-sharing':
        "unreviewed",
    'GET /vendors/market-sharing':
        "unreviewed",
    'GET /vendors/{vendor_id}':
        "unreviewed",
    'GET /work-orders':
        "unreviewed",
    'GET /work-orders/{work_order_id}':
        "unreviewed",
    'GET /work-orders/{work_order_id}/attachments/{attachment_id}':
        "unreviewed",
    'PATCH /alerts/triggers/{trigger_id:int}':
        "unreviewed",
    'POST /activity/restore-group/{group_id}':
        "unreviewed",
    'POST /activity/restore/{event_id}':
        "unreviewed",
    'POST /admin/alert-routing/custom-topics':
        "unreviewed",
    'POST /admin/alert-routing/persona-groups':
        "unreviewed",
    'POST /admin/bot-instances':
        "unreviewed",
    'POST /ai/actions/{proposal_id}/approve':
        "unreviewed",
    'POST /ai/actions/{proposal_id}/reject':
        "unreviewed",
    'POST /ai/actions/{proposal_id}/rows/edit':
        "unreviewed",
    'POST /ai/actions/{proposal_id}/rows/remove':
        "unreviewed",
    'POST /ai/actions/{proposal_id}/undo':
        "unreviewed",
    'POST /ai/feedback/regenerate':
        "unreviewed",
    'POST /ai/feedback/thumbs-down':
        "unreviewed",
    'POST /ai/feedback/thumbs-up':
        "unreviewed",
    'POST /alerts/triggers':
        "unreviewed",
    'POST /extension/connect':
        "unreviewed",
    'POST /maintenance/tasks/{task_id}/attachment':
        "unreviewed",
    'POST /user/account/delete/cancel':
        "unreviewed",
    'POST /user/account/delete/confirm':
        "unreviewed",
    'POST /user/account/delete/request':
        "unreviewed",
    'POST /user/google/link':
        "unreviewed",
    'POST /user/password':
        "unreviewed",
    'POST /user/sessions/terminate-others':
        "the caller's own row — the path names them, nobody else",
    'POST /user/telegram/link/init':
        "unreviewed",
    'POST /vendors':
        "unreviewed",
    'POST /vendors/directory/{entry_id}/review':
        "unreviewed",
    'POST /vendors/{loser_id}/merge-into/{winner_id}':
        "unreviewed",
    'POST /vendors/{vendor_id}/link-directory/{entry_id}':
        "unreviewed",
    'POST /work-orders/{work_order_id}/attachments':
        "unreviewed",
    'PUT /admin/alert-routing/persona-topics/{persona}/{alert_type}':
        "unreviewed",
    'PUT /admin/alert-routing/persona-topics/{persona}/{alert_type}/subtypes':
        "unreviewed",
    'PUT /ai/tier':
        "unreviewed",
    'PUT /ai/user-model':
        "unreviewed",
    'PUT /page-layouts/{role}/{feature}':
        "unreviewed",
    'PUT /user/credentials':
        "unreviewed",
    'PUT /user/me/alerts':
        "the caller's own row — the path names them, nobody else",
    'PUT /user/preferences':
        "the caller's own row — the path names them, nobody else",
    'PUT /user/preferences/ui/{key}':
        "the caller's own row — the path names them, nobody else",
    'PUT /vendors/{vendor_id}':
        "unreviewed",
}


def test_no_route_is_unauthenticated_unless_it_is_listed(routes):
    missing = sorted(
        _key(m, p) for m, p, kind in routes
        if kind == "public" and _key(m, p) not in PUBLIC
    )
    assert not missing, (
        "these routes have no authentication dependency at all:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd the gate the route needs, or add it to PUBLIC with the "
          "reason it must be reachable by anyone."
    )


def test_a_public_entry_that_is_now_gated_is_removed(routes):
    """The half most ratchets forget. An entry whose route is gated —
    or gone — stops describing anything and starts sheltering the next
    route that lands on the same path."""
    live = {_key(m, p) for m, p, kind in routes if kind == "public"}
    stale = sorted(set(PUBLIC) - live)
    assert not stale, (
        "no longer unauthenticated (or no longer mounted) — remove from "
        "PUBLIC:\n  " + "\n  ".join(stale)
    )


def test_every_system_route_requires_the_system_owner(routes):
    """No allow-list, and none is needed: every ``/system/*`` route
    already carries it. The system layer is operator-only by
    construction, and this is what keeps a route from wandering out of
    that promise — including the operator-login routes, which live under
    ``/auth/`` precisely because they cannot require what they grant."""
    ungated = sorted(
        f"{_key(m, p)}  [{kind}]" for m, p, kind in routes
        if (p == "/system" or p.startswith("/system/"))
        and kind != "system_owner"
        and _key(m, p) not in SYSTEM_MACHINE_ROUTES
    )
    assert not ungated, (
        "these /system/* routes do not require the system owner:\n  "
        + "\n  ".join(ungated)
        + "\n\nA system route serves 4truck the operator and nobody else. "
          "If this one genuinely serves a customer, it does not belong "
          "under /system/."
    )


def test_every_tenant_route_carries_a_permission_gate_unless_listed(routes):
    unreviewed = sorted(
        _key(m, p) for m, p, kind in routes
        if kind == "auth_only" and _key(m, p) not in AUTH_ONLY
    )
    assert not unreviewed, (
        "these routes prove only that the caller is signed in — any role "
        "reaches them:\n  " + "\n  ".join(unreviewed)
        + "\n\nAdd the permission the route needs, or add it to AUTH_ONLY "
          "with the reason every signed-in person may use it."
    )


def test_an_auth_only_entry_that_is_now_gated_is_removed(routes):
    """The queue only shrinks: a route that gained its permission must
    leave the list, or the exemption outlives the reason."""
    live = {_key(m, p) for m, p, kind in routes if kind == "auth_only"}
    stale = sorted(set(AUTH_ONLY) - live)
    assert not stale, (
        "now gated (or no longer mounted) — remove from AUTH_ONLY:\n  "
        + "\n  ".join(stale)
    )


def test_a_machine_route_that_is_now_held_by_the_operator_is_removed(routes):
    """The stale half, for the smallest and most dangerous list: an
    entry that no longer names a machine-gated /system/ route is an
    unclaimed exemption sitting on a path anybody may later take."""
    live = {_key(m, p) for m, p, kind in routes
            if kind == "machine" and (p == "/system" or p.startswith("/system/"))}
    stale = sorted(set(SYSTEM_MACHINE_ROUTES) - live)
    assert not stale, (
        "no longer a machine-gated /system/ route — remove from "
        "SYSTEM_MACHINE_ROUTES:\n  " + "\n  ".join(stale)
    )
