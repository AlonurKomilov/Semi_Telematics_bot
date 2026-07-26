"""Alert delivery profiles — declared lifecycle per type.

Pins the owner's 2026-07-26 choice ("Recommendations + Board rows"):

  • maintenance / documents / scorecard → Board rows + personal
    channels, NOT ackable; samsara_sync/system stay group-only;
    unknown types fail safe to group-only
  • post_alert_to_topic runs the profile lifecycle (history upsert +
    opt-in personal fanout with the history correlation key) REGARDLESS
    of group routing — an account with no groups still gets its Board
    row; the return value keeps the callers' "posted to a group?"
    DM-fallback contract
  • dedup_key rides as alert_subkey (per-task / per-day occurrence
    buckets); group-only types write and fan out nothing
  • the new categories carry role-set audiences (documents → hr/owner/
    admin; scorecard → safety/fleet/owner/admin)

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.alerting  # noqa: F401 — registers categories
import capabilities.alerting.pipeline as pipeline_mod
from capabilities.alerting.pipeline import post_alert_to_topic
from capabilities.alerting.profiles import get_profile
from capabilities.notifications.categories import get_category


# ── the profile table itself ────────────────────────────────────────

def test_lite_trio_board_personal_not_ackable():
    for t in ("maintenance", "documents", "scorecard"):
        p = get_profile(t)
        assert (p.board, p.ackable, p.personal) == (True, False, True), t


def test_housekeeping_and_unknown_are_group_only():
    for t in ("samsara_sync", "system", "brand_new_type"):
        p = get_profile(t)
        assert (p.board, p.ackable, p.personal) == (False, False, False), t


def test_heavy_family_keeps_full_lifecycle():
    for t in ("fault", "health", "fuel", "events", "parking", "camera"):
        assert get_profile(t) == get_profile("fault"), t


# ── lifecycle in post_alert_to_topic ────────────────────────────────

class _Tenant:
    def __init__(self):
        self.upserts: list[dict] = []

    async def upsert_alert_history(self, account_id, alert_type, vehicle_id,
                                   vehicle_name, *, last_detail="",
                                   severity="warning", location="",
                                   alert_subkey=""):
        self.upserts.append({
            "alert_type": alert_type, "vehicle_id": vehicle_id,
            "vehicle_name": vehicle_name, "severity": severity,
            "alert_subkey": alert_subkey, "last_detail": last_detail,
        })
        return {"id": 900 + len(self.upserts)}


class _Dispatch:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, db, account_id, content, *, channels=None,
                       recipient_filter=None, correlation_key=""):
        self.calls.append({"content": content, "channels": channels,
                           "correlation_key": correlation_key})
        return []


async def _post(monkeypatch, *, alert_type, tenant=None, dispatch=None, **kw):
    tenant = tenant or _Tenant()
    dispatch = dispatch or _Dispatch()

    async def _resolve(**_kw):
        return []                                # no groups configured
    monkeypatch.setattr(
        "capabilities.alerting.routing_resolver.resolve_alert_targets",
        _resolve)
    monkeypatch.setattr("capabilities.notifications.dispatch", dispatch)

    async def _tdb(aid):
        return tenant
    monkeypatch.setattr(pipeline_mod, "get_tenant_db", _tdb)
    monkeypatch.setattr(pipeline_mod, "get_platform_db", lambda: object())
    monkeypatch.setattr(pipeline_mod, "_FORUM_ROUTING_ENABLED", True)
    posted = await post_alert_to_topic(
        None, account_id=1, alert_type=alert_type,
        text="<b>Overdue Maintenance</b>\nTruck 105", severity="warning",
        **kw)
    return posted, tenant, dispatch


@pytest.mark.asyncio
async def test_board_row_and_personal_fanout_without_groups(monkeypatch):
    posted, tenant, dispatch = await _post(
        monkeypatch, alert_type="maintenance",
        subject_id="v-105", subject_name="Truck 105",
        dedup_key="task:7")
    assert posted is False                       # no groups → DM-fallback contract
    row = tenant.upserts[0]
    assert row["vehicle_id"] == "v-105"
    assert row["alert_subkey"] == "task:7"       # per-task occurrence bucket
    assert row["last_detail"] == "Overdue Maintenance"   # plain first line
    call = dispatch.calls[0]
    assert call["channels"] == ("telegram_dm", "email", "web_push")
    assert call["correlation_key"] == "alert:901"
    assert call["content"].category == "alert.maintenance"
    assert "<b>" not in call["content"].body     # raw plain text


@pytest.mark.asyncio
async def test_group_only_type_writes_and_sends_nothing(monkeypatch):
    posted, tenant, dispatch = await _post(
        monkeypatch, alert_type="samsara_sync",
        subject_id="x", subject_name="X")
    assert tenant.upserts == [] and dispatch.calls == []


@pytest.mark.asyncio
async def test_board_needs_a_subject(monkeypatch):
    # camera digest keeps passing no subject → NO lifecycle at all: no
    # Board row AND no personal fanout (camera's own per-sub loop owns
    # personal delivery — a matrix fanout here would double-DM).
    posted, tenant, dispatch = await _post(monkeypatch, alert_type="camera")
    assert tenant.upserts == []
    assert dispatch.calls == []


# ── category audiences ──────────────────────────────────────────────

def test_documents_category_audience():
    cat = get_category("alert.documents")
    assert cat is not None
    assert cat.audience("hr") and cat.audience("owner")
    assert not cat.audience("dispatcher")


def test_scorecard_category_audience():
    cat = get_category("alert.scorecard")
    assert cat is not None
    assert cat.audience("safety") and cat.audience("fleet")
    assert not cat.audience("hr")
