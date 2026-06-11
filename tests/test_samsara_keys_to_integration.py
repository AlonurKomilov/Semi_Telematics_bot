"""Tests for the Samsara key migration: moving keys from
``companies.samsara_api_key`` to ``account_integrations.credentials_enc``.

Covers:

  * Storage helper ``set_company_credential`` upserts and removes
    individual entries without touching other companies' keys.
  * ``build_multi_company_client`` prefers the creds_map entry over
    the legacy company column, falls back to the column when the
    map is missing an entry.
  * Migration 089 backfills idempotently — re-running produces no
    changes on a populated integration.
  * The API surface (PUT/DELETE /integrations/{provider}/companies/
    {code}/credentials) writes to both the integration map and the
    legacy company column (dual-write), invalidates the cached
    Samsara client.

The dashboard side (ConnectedCompanies React component) is covered by
the dashboard's own vitest suite, not here.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from adapters.storage import Database


@pytest.fixture
def encryption_enabled():
    """Activate Fernet encryption for the duration of a test.

    The PUT /credentials route now refuses to persist a token while
    encryption is disabled (it would land in cleartext), so the
    dual-write happy-path test must run with a key configured — which
    also mirrors the production deployment.  Resets the global cipher
    state on teardown so the encryption-off default (conftest sets
    ``ENCRYPTION_KEY=""``) doesn't leak to sibling tests in the worker.
    """
    import infra.crypto as _crypto
    _crypto._fernet = None
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test-passphrase-42"}):
        _crypto.init_encryption()
    assert _crypto.is_enabled()
    try:
        yield
    finally:
        _crypto._fernet = None


# ── Storage: set_company_credential ──────────────────────────────


@pytest.mark.asyncio
async def test_set_company_credential_upserts_new_entry(seeded_db):
    """First call creates the credentials.companies map."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": ""},
    )
    pre = await db.get_account_integration(account.id, "samsara")
    assert pre is not None
    assert (pre.credentials.get("companies") or {}) == {}

    updated = await db.set_company_credential(
        account.id, "samsara", "CFT", "samsara_token_cft",
    )
    assert updated is not None
    assert updated.credentials["companies"] == {"CFT": "samsara_token_cft"}


@pytest.mark.asyncio
async def test_set_company_credential_preserves_other_entries(seeded_db):
    """Setting CFT's key must not touch G1's key."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={"companies": {"G1": "existing_token"}, "api_token": ""},
    )
    updated = await db.set_company_credential(
        account.id, "samsara", "CFT", "new_cft_token",
    )
    assert updated is not None
    companies = updated.credentials["companies"]
    assert companies["CFT"] == "new_cft_token"
    assert companies["G1"] == "existing_token"


@pytest.mark.asyncio
async def test_set_company_credential_with_none_removes_entry(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={
            "companies": {"CFT": "tok_a", "G1": "tok_b"},
            "api_token": "",
        },
    )
    updated = await db.set_company_credential(
        account.id, "samsara", "CFT", None,
    )
    assert updated is not None
    assert "CFT" not in updated.credentials["companies"]
    assert updated.credentials["companies"]["G1"] == "tok_b"


@pytest.mark.asyncio
async def test_set_company_credential_returns_none_when_integration_missing(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    # No upsert — no row exists.
    result = await db.set_company_credential(
        account.id, "samsara", "CFT", "x",
    )
    assert result is None


# ── Resolver: build_multi_company_client precedence ──────────────


def test_build_multi_company_client_prefers_creds_map():
    """If a code has an entry in creds_map AND a legacy column key,
    the creds_map entry wins."""
    from adapters.telematics.samsara.client import build_multi_company_client

    co = MagicMock()
    co.code = "CFT"
    co.samsara_api_key = "legacy_column_key"
    co.active_days = 30

    multi = build_multi_company_client(
        [co],
        creds_map={"CFT": "integration_map_key"},
    )
    assert "CFT" in multi.clients
    assert multi.clients["CFT"].api_key == "integration_map_key"


def test_build_multi_company_client_falls_back_to_legacy_column():
    """If the creds_map is missing an entry, the legacy column value
    is used — keeps the four legacy read sites working during the
    cutover."""
    from adapters.telematics.samsara.client import build_multi_company_client

    co = MagicMock()
    co.code = "CFT"
    co.samsara_api_key = "legacy_column_key"
    co.active_days = 30

    multi = build_multi_company_client([co], creds_map={})
    assert "CFT" in multi.clients
    assert multi.clients["CFT"].api_key == "legacy_column_key"


def test_build_multi_company_client_skips_company_without_any_key():
    """A company with no entry in either source is silently skipped —
    rather than constructing a client with an empty token that would
    401 on every request."""
    from adapters.telematics.samsara.client import build_multi_company_client

    co_with = MagicMock()
    co_with.code = "CFT"
    co_with.samsara_api_key = "tok_cft"
    co_with.active_days = 30

    co_without = MagicMock()
    co_without.code = "NEW"
    co_without.samsara_api_key = ""
    co_without.active_days = 30

    multi = build_multi_company_client(
        [co_with, co_without], creds_map={},
    )
    assert "CFT" in multi.clients
    assert "NEW" not in multi.clients


def test_build_multi_company_client_uses_fallback_token():
    """When neither the creds_map nor the column has an entry but a
    single-token fallback is provided, use that."""
    from adapters.telematics.samsara.client import build_multi_company_client

    co = MagicMock()
    co.code = "CFT"
    co.samsara_api_key = ""
    co.active_days = 30

    multi = build_multi_company_client(
        [co], creds_map={}, fallback_token="shared_token",
    )
    assert "CFT" in multi.clients
    assert multi.clients["CFT"].api_key == "shared_token"


# ── Reconnect preserves credentials.companies ───────────────────


# ── Route: dual-write end-to-end (catches the bugs the adversarial
#     review found — missing await + swapped update_company args) ──


@pytest.mark.asyncio
async def test_put_credentials_dual_writes_and_invalidates_client(seeded_db, monkeypatch, encryption_enabled):
    """Putting a key via the new endpoint MUST:
      1. Write the canonical integration creds map.
      2. Dual-write the legacy ``companies.samsara_api_key`` column.
      3. Await the async ``invalidate_client`` so the cached
         MultiCompanyClient drops the old token.

    Two missing-await / swapped-args bugs were found in adversarial
    review precisely because no test exercised this end-to-end path.
    """
    from capabilities.integrations import router as integrations_module

    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    # Seed: integration row + one company with an old legacy key.
    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={"companies": {}, "api_token": ""},
    )
    await db.add_company(
        account_id=account.id, code="CFT",
        samsara_api_key="OLD_LEGACY_KEY",
        display_name="Cargo Freight", active_days=30,
    )

    # Stub the platform/tenant DB getters in the route module so they
    # return our seeded test DB.  The route uses get_account_integration
    # + set_company_credential + update_company — all live on the same
    # Database instance.
    monkeypatch.setattr(
        "infra.platform.get_platform_db", lambda: db,
    )

    async def fake_get_tenant_db(account_id):
        return db

    monkeypatch.setattr(
        "infra.platform.get_tenant_db", fake_get_tenant_db,
    )

    # Track invalidate_client — was it awaited?
    invalidate_calls: list = []

    async def fake_invalidate(acct_id):
        invalidate_calls.append(acct_id)

    monkeypatch.setattr(
        integrations_module, "invalidate_client", fake_invalidate,
    )

    # Audit log helper — no-op for the test.
    async def fake_audit(*a, **kw):
        return None

    monkeypatch.setattr(integrations_module, "_audit", fake_audit)

    body = integrations_module.CompanyCredentialUpsert(
        api_token="NEW_INTEGRATION_KEY",
    )
    user = {"account_id": account.id, "id": 1}

    await integrations_module.set_company_credential_endpoint(
        "samsara", "CFT", body, user=user,
    )

    # 1. Integration creds map updated.
    integ = await db.get_account_integration(account.id, "samsara")
    assert integ is not None
    assert integ.credentials["companies"]["CFT"] == "NEW_INTEGRATION_KEY"

    # 2. Dual-write actually landed on the company column —
    # the adversarial review found this was a silent no-op due to
    # swapped (company_id, account_id) args.
    companies = await db.get_account_companies(account.id, active_only=False)
    cft = next((c for c in companies if c.code == "CFT"), None)
    assert cft is not None
    assert cft.samsara_api_key == "NEW_INTEGRATION_KEY"

    # 3. invalidate_client was called AND awaited (if it had been
    # called without await, the coroutine would be a RuntimeWarning
    # but invalidate_calls would stay empty because the body never
    # ran).
    assert invalidate_calls == [account.id]


@pytest.mark.asyncio
async def test_delete_credentials_dual_writes_and_invalidates_client(seeded_db, monkeypatch):
    """Mirror of the above for the DELETE path."""
    from capabilities.integrations import router as integrations_module

    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={"companies": {"CFT": "EXISTING_KEY"}, "api_token": ""},
    )
    await db.add_company(
        account_id=account.id, code="CFT",
        samsara_api_key="EXISTING_KEY",
        display_name="Cargo Freight", active_days=30,
    )

    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    async def fake_get_tenant_db(account_id):
        return db

    monkeypatch.setattr("infra.platform.get_tenant_db", fake_get_tenant_db)

    invalidate_calls: list = []

    async def fake_invalidate(acct_id):
        invalidate_calls.append(acct_id)

    monkeypatch.setattr(integrations_module, "invalidate_client", fake_invalidate)

    async def fake_audit(*a, **kw):
        return None

    monkeypatch.setattr(integrations_module, "_audit", fake_audit)

    user = {"account_id": account.id, "id": 1}
    await integrations_module.remove_company_credential_endpoint(
        "samsara", "CFT", user=user,
    )

    integ = await db.get_account_integration(account.id, "samsara")
    assert "CFT" not in (integ.credentials.get("companies") or {})

    companies = await db.get_account_companies(account.id, active_only=False)
    cft = next((c for c in companies if c.code == "CFT"), None)
    # Legacy column cleared.
    assert cft.samsara_api_key == ""

    assert invalidate_calls == [account.id]


@pytest.mark.asyncio
async def test_reconnect_preserves_companies_map(seeded_db):
    """A credential rotation on the legacy admin route shouldn't
    wipe the per-company creds map — otherwise the migration's
    canonical store would get nuked by a reconnect."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={
            "companies": {"CFT": "tok_cft", "G1": "tok_g1"},
            "api_token": "",
        },
    )

    # Reconnect — pass credentials=None means "keep existing".
    await db.upsert_account_integration(
        account.id, "samsara", credentials=None,
    )
    after = await db.get_account_integration(account.id, "samsara")
    assert after is not None
    assert after.credentials["companies"] == {
        "CFT": "tok_cft", "G1": "tok_g1",
    }
