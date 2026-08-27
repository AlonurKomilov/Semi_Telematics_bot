"""Fail-closed guard: integration credential endpoints refuse to persist
a third-party API token while encryption is disabled.

When ``ENCRYPTION_KEY`` is unset, ``infra.crypto`` is a deliberate
plaintext pass-through — a migration affordance for the legacy single-key
column.  That must NOT extend to a fresh Samsara token an operator enters
through the integration API: ``POST /integrations/{p}/connect`` and
``PUT  /integrations/{p}/companies/{code}/credentials`` fail closed with a
503 instead of writing the key to the DB in cleartext.

The guard runs before any DB/provider access, so these exercise the route
handlers directly without a DB harness.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import infra.crypto as crypto
from capabilities.integrations.samsara.router import (
    CompanyCredentialUpsert,
    set_company_credential_endpoint,
)
from capabilities.integrations.shared.helpers import (
    ConnectRequest,
    guard_credential_encryption as _guard_credential_encryption,
)
from capabilities.integrations.shared.router import connect_integration


@pytest.fixture
def encryption_disabled():
    crypto._fernet = None
    with patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
        crypto.init_encryption()
    assert not crypto.is_enabled()
    try:
        yield
    finally:
        crypto._fernet = None


@pytest.fixture
def encryption_enabled():
    crypto._fernet = None
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test-passphrase-42"}):
        crypto.init_encryption()
    assert crypto.is_enabled()
    try:
        yield
    finally:
        crypto._fernet = None


_OWNER = {"account_id": 1, "id": 1, "role": "owner"}


class TestGuardHelper:
    def test_raises_503_when_disabled(self, encryption_disabled):
        with pytest.raises(HTTPException) as exc:
            _guard_credential_encryption()
        assert exc.value.status_code == 503
        assert "ENCRYPTION_KEY" in exc.value.detail

    def test_noop_when_enabled(self, encryption_enabled):
        assert _guard_credential_encryption() is None


@pytest.mark.asyncio
class TestConnectEndpointGuard:
    async def test_connect_refuses_plaintext_token(self, encryption_disabled):
        """A fresh connect with a token must 503 before the row is written."""
        with pytest.raises(HTTPException) as exc:
            await connect_integration(
                "samsara",
                ConnectRequest(credentials={"api_token": "live-secret"}),
                user=_OWNER,
            )
        assert exc.value.status_code == 503
        assert "ENCRYPTION_KEY" in exc.value.detail


@pytest.mark.asyncio
class TestSetCompanyCredentialGuard:
    async def test_put_refuses_plaintext_token(self, encryption_disabled):
        """The per-company PUT always carries a secret (api_token is
        min_length=1) — it must 503 before any DB write."""
        with pytest.raises(HTTPException) as exc:
            await set_company_credential_endpoint(
                "ACME",
                CompanyCredentialUpsert(api_token="live-secret"),
                user=_OWNER,
            )
        assert exc.value.status_code == 503
        assert "ENCRYPTION_KEY" in exc.value.detail
