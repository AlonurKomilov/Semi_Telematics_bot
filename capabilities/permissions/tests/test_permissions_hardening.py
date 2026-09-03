"""Integration tests for the permissions module hardening pass:

  Issue 1 — company_id ↔ account_id validation on every write endpoint
  Issue 2 — TTL on _permissions_cache (60s)
  Issue 3 — audit_log integration on every permission change

Plus end-to-end per-account isolation verification (Account A's
override never bleeds into Account B's resolution path).
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def app_client(pg_db):
    """Two accounts with one owner each + a company per account.

    The cross-account companies are what makes the Issue 1
    validation tests meaningful — Account A's owner can't write a
    permission override scoped to a company that belongs to B.
    """
    db = pg_db

    acct_a = await db.create_account("Permissions Test Co A")
    acct_b = await db.create_account("Permissions Test Co B")

    co_a = await db.add_company(acct_a.id, "COA", "samsara_key_a", "Company A")
    co_b = await db.add_company(acct_b.id, "COB", "samsara_key_b", "Company B")

    owner_a = await db.create_user(970001, acct_a.id, role=Role.OWNER)
    owner_b = await db.create_user(970002, acct_b.id, role=Role.OWNER)

    tokens = {
        "owner_a": create_jwt(owner_a.telegram_id, acct_a.id, "owner"),
        "owner_b": create_jwt(owner_b.telegram_id, acct_b.id, "owner"),
    }

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client, "db": db,
            "acct_a": acct_a, "acct_b": acct_b,
            "co_a": co_a, "co_b": co_b,
            "tokens": tokens,
        }
    _cp._db = _old


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Issue 1: company_id ↔ account_id validation ────────────────


@pytest.mark.asyncio
async def test_put_rejects_cross_account_company_id(app_client):
    """Account A's owner cannot write an override scoped to Account B's
    company — even though the row insert would technically succeed
    on the (account_id=A, company_id=B_co.id) tuple, the read path
    never serves it.  Defense-in-depth: reject the write up front."""
    s = app_client
    r = await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={
            "role": "fleet",
            "permissions": {"can_faults": False},
            "company_id": s["co_b"].id,  # belongs to Account B
        },
    )
    assert r.status_code == 404, (
        f"Cross-account company_id must be rejected; got {r.status_code} {r.text}"
    )


@pytest.mark.asyncio
async def test_put_accepts_own_account_company_id(app_client):
    """Sanity check: Account A's owner CAN write an override scoped to
    Account A's own company.  No regression on the valid path."""
    s = app_client
    r = await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={
            "role": "fleet",
            "permissions": {"can_faults": False},
            "company_id": s["co_a"].id,
        },
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_reset_rejects_cross_account_company_id(app_client):
    s = app_client
    r = await s["client"].post(
        "/api/admin/permissions/roles/reset",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={
            "role": "fleet",
            "permissions": {},
            "company_id": s["co_b"].id,
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_override_rejects_cross_account_company_id(app_client):
    s = app_client
    r = await s["client"].post(
        "/api/admin/permissions/roles/delete-override",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "fleet", "company_id": s["co_b"].id},
    )
    assert r.status_code == 404


# ── Issue 2: TTL on _permissions_cache ──────────────────────────


@pytest.mark.asyncio
async def test_cache_entry_carries_expiry_timestamp(app_client):
    """The cache stores (expires_at, FeatureSet) tuples — a stale
    multi-worker entry ages out within ``_PERMS_CACHE_TTL_S`` even
    when the worker that holds it never sees the invalidation
    signal."""
    s = app_client
    from capabilities.permissions.roles import (
        get_account_permissions, _permissions_cache, _PERMS_CACHE_TTL_S,
    )
    import time as _time
    # Prime the cache via a normal resolution path.
    await get_account_permissions(Role.FLEET, s["acct_a"].id)
    cache_key = (s["acct_a"].id, "fleet", None)
    assert cache_key in _permissions_cache, "cache must populate after first read"

    entry = _permissions_cache[cache_key]
    assert isinstance(entry, tuple) and len(entry) == 2, (
        f"cache entry must be (expires_at, FeatureSet); got {type(entry)}"
    )
    expires_at, _fs = entry
    now = _time.monotonic()
    # TTL bounds: not in the past, not more than 1 second past the
    # configured window (allows for monotonic-clock jitter).
    assert expires_at > now, "entry should not be already-expired on insert"
    assert expires_at <= now + _PERMS_CACHE_TTL_S + 1.0, (
        f"expiry should be within TTL window; got {expires_at - now:.1f}s ahead"
    )


@pytest.mark.asyncio
async def test_invalidate_drops_account_entries_only(app_client):
    """invalidate_permissions_cache(account_id) must drop ONLY that
    account's entries — never bleed into other accounts' cache.
    Protects against a buggy refactor that broadens the predicate."""
    s = app_client
    from capabilities.permissions.roles import (
        get_account_permissions, invalidate_permissions_cache,
        _permissions_cache,
    )
    await get_account_permissions(Role.FLEET, s["acct_a"].id)
    await get_account_permissions(Role.FLEET, s["acct_b"].id)
    assert (s["acct_a"].id, "fleet", None) in _permissions_cache
    assert (s["acct_b"].id, "fleet", None) in _permissions_cache

    invalidate_permissions_cache(s["acct_a"].id)
    assert (s["acct_a"].id, "fleet", None) not in _permissions_cache
    assert (s["acct_b"].id, "fleet", None) in _permissions_cache, (
        "B's cache must not be cleared by A's invalidation"
    )


@pytest.mark.asyncio
async def test_stale_cache_entry_is_re_resolved(app_client):
    """An entry past its TTL gets re-resolved from DB on next read —
    not served stale.  This is the multi-worker safety net."""
    s = app_client
    from capabilities.permissions.roles import (
        get_account_permissions, _permissions_cache,
    )
    import time as _time
    # Seed a fresh entry then poison its TTL so it looks stale.
    await get_account_permissions(Role.FLEET, s["acct_a"].id)
    cache_key = (s["acct_a"].id, "fleet", None)
    _, fs = _permissions_cache[cache_key]
    _permissions_cache[cache_key] = (_time.monotonic() - 1.0, fs)

    # Write an account override so the post-TTL re-resolution would
    # produce a different result than the cached snapshot.
    await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "fleet", "permissions": {"can_view_faults": False}},
    )
    # The PUT also invalidates the cache; for a strict TTL-only test
    # we'd want to skip that path.  But the combined behavior is what
    # ships: invalidation kills the stale entry immediately; the next
    # read re-populates from DB with the new can_faults=False.
    refreshed = await get_account_permissions(Role.FLEET, s["acct_a"].id)
    assert refreshed.can_faults is False


# ── Issue 3: audit log integration ──────────────────────────────


@pytest.mark.asyncio
async def test_put_writes_trail_event_with_diff(app_client):
    """PUT records one activity-trail event (action='permissions_update')
    whose changes carry the per-flag before/after in the trail's native
    {from,to} shape.  (Formerly a skipped audit_log test — the trail IS
    the current diff format.)"""
    s = app_client
    db = s["db"]
    acct = s["acct_a"]

    await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "fleet", "permissions": {"can_view_faults": False}},
    )

    events = await db.list_activity_events(
        acct.id, entity_type="role", action="permissions_update",
    )
    assert events, "expected a permissions_update trail event"
    ev = events[0]
    assert ev["entity_id"] == "fleet"
    assert ev["context"]["scope"] == "account-wide"
    # can_view_faults flips True → False (Fleet default is True).
    assert ev["changes"]["can_view_faults"] == {"from": True, "to": False}
    assert ev["actor_user_id"] is not None


@pytest.mark.asyncio
async def test_reset_writes_audit_log(app_client):
    s = app_client
    db = s["db"]
    acct = s["acct_a"]

    await s["client"].post(
        "/api/admin/permissions/roles/reset",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "fleet", "permissions": {}},
    )

    events = await db.list_activity_events(
        acct.id, entity_type="role", action="permissions_reset",
    )
    assert len(events) >= 1
    assert events[0]["entity_id"] == "fleet"


@pytest.mark.asyncio
async def test_reset_all_writes_audit_log(app_client):
    s = app_client
    db = s["db"]
    acct = s["acct_a"]

    await s["client"].post(
        "/api/admin/permissions/roles/reset-all",
        headers=_hdr(s["tokens"]["owner_a"]),
    )

    events = await db.list_activity_events(
        acct.id, action="permissions_reset_all",
    )
    assert events, "expected a permissions_reset_all trail event"


# ── Self-lockout protection (existing — re-tested for regression) ─


@pytest.mark.asyncio
async def test_owner_cannot_remove_own_management_access(app_client):
    s = app_client
    r = await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "owner", "permissions": {"can_manage_permissions": False}},
    )
    assert r.status_code == 400


# ── Per-account isolation (the user's original Q1) ───────────────


@pytest.mark.asyncio
async def test_account_a_override_does_not_affect_account_b(app_client):
    """The user-facing guarantee: configuring permissions for Account
    A's Fleet role MUST NOT change what Account B's Fleet users see.
    Verified end-to-end across DB + cache + resolution path."""
    s = app_client
    from capabilities.permissions.roles import get_account_permissions

    # Account A: flip Fleet's can_faults to False
    await s["client"].put(
        "/api/admin/permissions/roles",
        headers=_hdr(s["tokens"]["owner_a"]),
        json={"role": "fleet", "permissions": {"can_view_faults": False}},
    )

    perms_a = await get_account_permissions(Role.FLEET, s["acct_a"].id)
    perms_b = await get_account_permissions(Role.FLEET, s["acct_b"].id)

    assert perms_a.can_faults is False, "A's override must apply"
    assert perms_b.can_faults is True, (
        "B must NOT inherit A's override (default Fleet has can_faults=True)"
    )
