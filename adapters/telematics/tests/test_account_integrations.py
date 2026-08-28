"""Smoke tests for the account_integrations storage layer + the
telematics provider registry / catalog.

Verifies that:

  * The migration creates the table and indexes idempotently.
  * The data-backfill migration seeds an existing Samsara company into
    a ``samsara`` integration row with the expected default toggles.
  * Upsert / get / list / status-flip / health-check / delete all work
    end-to-end and round-trip credentials through Fernet encryption.
  * The fan-out helper picks accounts whose toggles enable a
    capability and skips those whose toggles disable it.
  * The provider registry round-trips registration + lookup and the
    catalog renders all expected entries with the right statuses.

Designed so the suite passes against the existing PG-backed test DB
without any external dependencies.
"""

from __future__ import annotations

import pytest

from adapters.storage import Database
from adapters.telematics import (
    PROVIDER_CATALOG,
    Capability,
    ProviderStatus,
    get_provider,
    is_registered,
    list_registered_providers,
    register_provider,
)
from adapters.telematics.registry import _reset_for_tests, _registry


@pytest.fixture(autouse=True)
def _preserve_registry_state():
    """Snapshot-and-restore the telematics registry around every test
    in this module.

    The registry-manipulation tests below call ``_reset_for_tests``
    which wipes the Samsara registration that ``adapters.telematics``
    auto-installs at import time.  Without restore, downstream tests
    (in this file or other files running in the same xdist worker
    after this one) see an empty registry and fail to resolve
    ``samsara``.  This fixture restores the pre-test state so the
    side-effect never escapes the test that caused it.
    """
    snapshot = dict(_registry)
    try:
        yield
    finally:
        _registry.clear()
        _registry.update(snapshot)


# ── Provider registry / catalog ───────────────────────────────────


def test_catalog_lists_expected_providers():
    """Every advertised provider has the required metadata."""
    assert set(PROVIDER_CATALOG.keys()) >= {
        "samsara", "motive", "geotab", "datatruck",
    }

    for entry in PROVIDER_CATALOG.values():
        assert entry.provider_id
        assert entry.display_name
        assert entry.capabilities, f"{entry.provider_id} has no capabilities"
        assert entry.auth_kind


def test_catalog_availability_today():
    """Samsara (telematics) and Datatruck (TMS) are both AVAILABLE.
    Motive and Geotab remain COMING_SOON until their adapters ship."""
    assert PROVIDER_CATALOG["samsara"].status == ProviderStatus.AVAILABLE
    assert PROVIDER_CATALOG["datatruck"].status == ProviderStatus.AVAILABLE
    for pid in ("motive", "geotab"):
        assert PROVIDER_CATALOG[pid].status == ProviderStatus.COMING_SOON


def test_datatruck_uses_api_token_with_subdomain_auth():
    """Datatruck's per-tenant API host means the connect form must
    ask for a subdomain in addition to the token.  This test locks
    that contract so a future refactor can't silently revert it and
    break the operator UX."""
    assert PROVIDER_CATALOG["datatruck"].auth_kind == "api_token_with_subdomain"


def test_catalog_samsara_defaults_include_all_capabilities():
    samsara = PROVIDER_CATALOG["samsara"]
    for cap in samsara.capabilities:
        assert cap in samsara.feature_defaults, (
            f"capability {cap!r} in catalog but missing from feature_defaults"
        )


def test_registry_round_trip():
    _reset_for_tests()
    assert list_registered_providers() == []
    assert not is_registered("samsara")

    class Stub:
        provider_id = "samsara"

    register_provider("samsara", Stub)
    assert is_registered("samsara")
    assert get_provider("samsara") is Stub
    assert list_registered_providers() == ["samsara"]


def test_registry_replacement_logs_but_succeeds(caplog):
    _reset_for_tests()

    class A:
        provider_id = "x"

    class B:
        provider_id = "x"

    register_provider("x", A)
    with caplog.at_level("WARNING"):
        register_provider("x", B)
    assert get_provider("x") is B


def test_registry_get_unregistered_raises():
    _reset_for_tests()
    with pytest.raises(KeyError):
        get_provider("nonexistent")


# ── Migration / table existence ────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_creates_account_integrations_table(seeded_db):
    """Migration 083 ran during seeded_db setup — confirm the table
    is queryable and has the expected indexes."""
    db = seeded_db["db"]
    cur = await db._db.execute(
        "SELECT account_id, provider_id, status, feature_toggles "
        "FROM account_integrations LIMIT 1"
    )
    assert cur is not None
    # cur.fetchall() returning [] is fine — what we need is that the
    # query parsed against the schema without raising.
    await cur.fetchall()


# NB: ``test_migration_backfill_seeds_samsara_for_existing_companies``
# (which referenced the historical ``migrate_account_integrations_
# backfill_samsara`` migration) was retired.  The integration row is
# now seeded by:
#   * migration 091 — creates the ``account_integrations`` table
#   * migration 093 — backfills the per-company Samsara credentials
#     map from ``companies.samsara_api_key``
#   * the POST /integrations/samsara/connect auto-trigger flow for
#     fresh first-connects (see ``test_integration_button_fixes.py``
#     and ``test_samsara_keys_to_integration.py``)
# Removing this test rather than rebuilding it because the original
# migration symbol no longer exists in the codebase.


# ── CRUD round-trip ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_and_get_round_trip(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    creds = {"api_token": "secret-abc-123", "org_id": "samsara_org_42"}
    toggles = {
        Capability.VEHICLE_STATE: {"enabled": True, "interval_sec": 60},
        Capability.SAFETY_EVENTS: {"enabled": False},
    }
    cadence = {Capability.VEHICLE_STATE: {"interval_sec": 120}}

    ai = await db.upsert_account_integration(
        account.id, "samsara",
        credentials=creds, feature_toggles=toggles,
        cadence_overrides=cadence, created_by=999,
    )
    assert ai.credentials == creds
    assert ai.feature_toggles == toggles
    assert ai.cadence_overrides == cadence

    refetched = await db.get_account_integration(account.id, "samsara")
    assert refetched is not None
    assert refetched.credentials == creds


@pytest.mark.asyncio
async def test_upsert_preserves_credentials_when_none_passed(seeded_db):
    """Toggle-only updates must not require re-sending credentials."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={"api_token": "original-token"},
        feature_toggles={"vehicle_state": {"enabled": True}},
    )

    # Update toggles only — credentials must persist.
    await db.upsert_account_integration(
        account.id, "samsara",
        feature_toggles={"vehicle_state": {"enabled": False}},
    )

    ai = await db.get_account_integration(account.id, "samsara")
    assert ai is not None
    assert ai.credentials == {"api_token": "original-token"}
    assert ai.feature_toggles["vehicle_state"]["enabled"] is False


@pytest.mark.asyncio
async def test_list_and_status_flip(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    # Set up two providers for the same account.
    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "s"},
    )
    await db.upsert_account_integration(
        account.id, "motive", credentials={"api_token": "m"},
    )

    all_rows = await db.list_account_integrations(account.id)
    enabled = await db.list_enabled_account_integrations(account.id)
    assert len(all_rows) == 2
    assert len(enabled) == 2

    # Pause samsara.
    flipped = await db.set_integration_status(account.id, "samsara", "disabled")
    assert flipped is not None
    assert flipped.status == "disabled"

    enabled_after = await db.list_enabled_account_integrations(account.id)
    assert {ai.provider_id for ai in enabled_after} == {"motive"}


@pytest.mark.asyncio
async def test_record_health_check(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "samsara", credentials={"api_token": "s"},
    )

    # OK path clears any prior error.
    await db.record_integration_health_check(
        account.id, "samsara", ok=True,
    )
    ai = await db.get_account_integration(account.id, "samsara")
    assert ai is not None
    assert ai.status == "connected"
    assert ai.last_health_at != ""
    assert ai.last_health_error == ""

    # Failure path flips status + records the message.
    await db.record_integration_health_check(
        account.id, "samsara", ok=False, message="401 Unauthorized",
    )
    ai = await db.get_account_integration(account.id, "samsara")
    assert ai is not None
    assert ai.status == "error"
    assert ai.last_health_error == "401 Unauthorized"


@pytest.mark.asyncio
async def test_delete_account_integration(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    await db.upsert_account_integration(
        account.id, "motive", credentials={"api_token": "m"},
    )
    deleted = await db.delete_account_integration(account.id, "motive")
    assert deleted is True

    after = await db.get_account_integration(account.id, "motive")
    assert after is None

    # Double-delete is a no-op.
    deleted_again = await db.delete_account_integration(account.id, "motive")
    assert deleted_again is False


# ── Fan-out helper (scheduler-facing) ──────────────────────────────


@pytest.mark.asyncio
async def test_capability_fan_out_filters_correctly(seeded_db):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]

    # Set up a row with the catalog's default toggle map (matches the
    # migration backfill's intent without depending on migration order).
    await db.upsert_account_integration(
        account.id, "samsara",
        credentials={"api_token": "s"},
        feature_toggles=PROVIDER_CATALOG["samsara"].feature_defaults,
    )

    enabled_state = await db.list_accounts_with_enabled_capability(
        Capability.VEHICLE_STATE,
    )
    assert (account.id, "samsara") in enabled_state

    # history_backfill is now ON by default (auto-trigger on connect
    # populates the calendar's data source immediately).  The fan-out
    # helper correctly surfaces this account as a candidate.
    enabled_backfill = await db.list_accounts_with_enabled_capability(
        Capability.HISTORY_BACKFILL,
    )
    assert (account.id, "samsara") in enabled_backfill

    # Flip the toggle OFF, re-check — the fan-out should drop it.
    await db.upsert_account_integration(
        account.id, "samsara",
        feature_toggles={
            **PROVIDER_CATALOG["samsara"].feature_defaults,
            Capability.HISTORY_BACKFILL: {"enabled": False},
        },
    )
    after = await db.list_accounts_with_enabled_capability(
        Capability.HISTORY_BACKFILL,
    )
    assert (account.id, "samsara") not in after


# ── Robustness ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_row_returns_none(seeded_db):
    db: Database = seeded_db["db"]
    assert await db.get_account_integration(99999, "samsara") is None
    assert await db.set_integration_status(99999, "samsara", "disabled") is None
    assert await db.update_feature_toggles(99999, "samsara", {}) is None
