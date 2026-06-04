"""Integration tests for capabilities.alerting.routing_resolver.

Exercises both modes end-to-end against a real Postgres DB so the
target list returned to the pipeline matches what the production
single_group / per_persona_groups paths will actually receive.

Critical paths:
  • single_group → exactly one target with message_thread_id set
  • per_persona_groups → primary persona target (thread=None)
  • CRITICAL severity → owner_admin aggregate cross-post added
  • mode flipped but tables empty → falls back to legacy lookup
"""

from __future__ import annotations

import pytest

from capabilities.alerting.routing_resolver import (
    resolve_alert_targets,
    AlertTarget,
    CRITICAL_SEVERITY,
)


@pytest.fixture
def _patch_platform_db(monkeypatch, seeded_db):
    """Wire ``infra.platform._db`` to the test DB so the resolver's
    ``get_platform_db()`` call (resolved through ``get_db()``) sees
    the per-test instance.  Patching the singleton instead of the
    accessor avoids the bind-time-vs-call-time gotcha where modules
    that imported ``get_platform_db`` already hold the original
    function reference."""
    import infra.platform as platform_mod
    db = seeded_db["db"]
    old = platform_mod._db
    platform_mod._db = db
    yield db
    platform_mod._db = old


@pytest.mark.asyncio
async def test_single_group_returns_one_legacy_target(_patch_platform_db, seeded_db):
    """Default-mode account with a configured alert_routing row →
    exactly one target with the topic thread_id set."""
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.upsert_forum_group(
        account_id=acct.id,
        chat_id=-100999,
        chat_title="forum",
        is_forum_enabled=True,
        created_by_user_id=seeded_db["owner"].id,
        setup_status="provisioned",
    )
    await db.upsert_alert_route(
        account_id=acct.id, alert_type="faults",
        chat_id=-100999, message_thread_id=42,
        topic_name_snapshot="Faults",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="warning",
    )
    assert len(targets) == 1
    t = targets[0]
    assert t.chat_id == -100999
    assert t.message_thread_id == 42
    assert t.is_aggregate is False
    assert t.persona == ""  # legacy path doesn't fill persona


@pytest.mark.asyncio
async def test_single_group_returns_empty_when_no_route(_patch_platform_db, seeded_db):
    """No alert_routing row → empty list, caller falls back to DM."""
    acct = seeded_db["account"]
    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="warning",
    )
    assert targets == []


@pytest.mark.asyncio
async def test_per_persona_warning_returns_primary_only(_patch_platform_db, seeded_db):
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-555, chat_title="Fleet",
    )
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-666, chat_title="Owners",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="warning",
    )
    assert len(targets) == 1
    t = targets[0]
    assert t.chat_id == -555
    assert t.message_thread_id is None
    assert t.persona == "fleet"
    assert t.is_aggregate is False


@pytest.mark.asyncio
async def test_per_persona_critical_adds_owner_aggregate(_patch_platform_db, seeded_db):
    """CRITICAL fans out to BOTH the primary persona group AND the
    owner_admin aggregate."""
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-555, chat_title="Fleet",
    )
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-666, chat_title="Owners",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity=CRITICAL_SEVERITY,
    )
    assert len(targets) == 2

    chat_ids = {t.chat_id for t in targets}
    assert chat_ids == {-555, -666}

    aggregate = next(t for t in targets if t.is_aggregate)
    assert aggregate.chat_id == -666
    assert aggregate.persona == "owner_admin"


@pytest.mark.asyncio
async def test_per_persona_critical_owner_only_skips_double_post(
    _patch_platform_db, seeded_db,
):
    """When the alert_type already routes to owner_admin as the primary
    (e.g. ``system``), we must NOT add the aggregate as a second
    target — that would post the same alert twice to the same group."""
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-666, chat_title="Owners",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="system", severity=CRITICAL_SEVERITY,
    )
    assert len(targets) == 1
    assert targets[0].chat_id == -666
    assert targets[0].is_aggregate is False  # primary, not aggregate


@pytest.mark.asyncio
async def test_per_persona_dedupes_same_chat_across_targets(
    _patch_platform_db, seeded_db,
):
    """Small fleet uses ONE Telegram group for both the operational
    persona and the owner aggregate.  The resolver must collapse the
    two targets into one to avoid double-posting."""
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-777, chat_title="The Group",
    )
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-777, chat_title="The Group",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity=CRITICAL_SEVERITY,
    )
    assert len(targets) == 1
    assert targets[0].chat_id == -777


@pytest.mark.asyncio
async def test_per_persona_falls_back_to_legacy_when_unconfigured(
    _patch_platform_db, seeded_db,
):
    """Operator flipped the mode but hasn't registered any persona
    groups yet.  The resolver MUST fall back to the legacy
    alert_routing lookup so the alert still ships instead of vanishing
    into a partial-migration limbo."""
    db = _patch_platform_db
    acct = seeded_db["account"]
    owner = seeded_db["owner"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    # Note: no persona groups registered.
    await db.upsert_forum_group(
        account_id=acct.id, chat_id=-100999, chat_title="legacy forum",
        is_forum_enabled=True, created_by_user_id=owner.id,
        setup_status="provisioned",
    )
    await db.upsert_alert_route(
        account_id=acct.id, alert_type="faults",
        chat_id=-100999, message_thread_id=42,
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="critical",
    )
    # Falls back to legacy lookup → exactly one legacy target.
    assert len(targets) == 1
    assert targets[0].chat_id == -100999
    assert targets[0].message_thread_id == 42


@pytest.mark.asyncio
async def test_unknown_account_id_returns_empty(_patch_platform_db):
    """Bogus account_id → empty list, never raises.  Defensive: the
    pipeline must never crash on a routing lookup."""
    targets = await resolve_alert_targets(
        account_id=999_999, alert_type="faults", severity="warning",
    )
    assert targets == []


def test_resolver_route_key_map_matches_pipeline():
    """The resolver duplicates pipeline._PIPELINE_TO_ROUTE_KEY for the
    legacy single_group lookup; drift between the two maps silently
    routes legacy-mode alerts to the wrong topic.  Parity check
    documented in routing_resolver.py asserted here."""
    from capabilities.alerting.routing_resolver import _PIPELINE_TO_ROUTE_KEY as resolver_map
    from capabilities.alerting.pipeline import _PIPELINE_TO_ROUTE_KEY as pipeline_map
    assert resolver_map == pipeline_map, (
        "routing_resolver._PIPELINE_TO_ROUTE_KEY drifted from "
        "pipeline._PIPELINE_TO_ROUTE_KEY — "
        f"only in resolver: {set(resolver_map) - set(pipeline_map)} | "
        f"only in pipeline: {set(pipeline_map) - set(resolver_map)}"
    )


@pytest.mark.asyncio
async def test_per_persona_only_owner_falls_back_to_legacy_for_non_critical(
    _patch_platform_db, seeded_db,
):
    """Half-migrated account: only owner_admin persona registered, the
    operational personas (dispatcher/safety/fleet/hr) are not.

    Non-CRITICAL fault → primary = fleet (missing), aggregate gate
    requires CRITICAL → targets=[].  Legacy lookup runs.

    CRITICAL fault → primary = fleet (still missing), aggregate =
    owner_admin (configured) → previously this returned aggregate-only,
    making CRITICAL route differently from non-CRITICAL during
    staged onboarding.  After the fix: primary missing ⇒
    primary_present=False ⇒ fall through to legacy lookup so both
    severities land in the same destination.
    """
    db = _patch_platform_db
    acct = seeded_db["account"]
    owner = seeded_db["owner"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-666, chat_title="Owners",
    )
    # Legacy forum routing is what should catch both severities.
    await db.upsert_forum_group(
        account_id=acct.id, chat_id=-100999, chat_title="legacy",
        is_forum_enabled=True, created_by_user_id=owner.id,
        setup_status="provisioned",
    )
    await db.upsert_alert_route(
        account_id=acct.id, alert_type="faults",
        chat_id=-100999, message_thread_id=42,
    )

    warn = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="warning",
    )
    crit = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="critical",
    )

    # Both severities now agree on the legacy destination.
    assert len(warn) == 1
    assert warn[0].chat_id == -100999
    assert len(crit) == 1
    assert crit[0].chat_id == -100999


@pytest.mark.asyncio
async def test_resolver_db_error_returns_empty_not_raise(
    _patch_platform_db, seeded_db, monkeypatch,
):
    """Resolver must never crash the pipeline.  When a DB call inside
    _resolve_per_persona raises, the wrapper returns [] (caller falls
    back to DM)."""
    db = _patch_platform_db
    acct = seeded_db["account"]
    await db.set_alert_routing_mode(acct.id, "per_persona_groups")

    async def _boom(_account_id, _persona):
        raise RuntimeError("simulated DB outage")
    monkeypatch.setattr(db, "get_persona_group", _boom)

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="critical",
    )
    assert targets == []


@pytest.mark.asyncio
async def test_severity_case_insensitive(_patch_platform_db, seeded_db):
    """severity='CRITICAL' must trigger the aggregate cross-post too."""
    db = _patch_platform_db
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-555, chat_title="Fleet",
    )
    await db.upsert_persona_group(
        account_id=acct.id, persona="owner_admin",
        chat_id=-666, chat_title="Owners",
    )

    targets = await resolve_alert_targets(
        account_id=acct.id, alert_type="faults", severity="CRITICAL",
    )
    assert len(targets) == 2
