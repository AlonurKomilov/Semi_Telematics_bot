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
                           "recipient_filter": recipient_filter,
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

def test_documents_category_audience_is_permission_derived():
    cat = get_category("alert.documents")
    assert cat is not None
    assert cat.audience("hr") and cat.audience("owner")
    # dispatcher holds neither can_manage_driver_docs nor
    # can_driver_docs_own in the static defaults.
    assert not cat.audience("dispatcher")


def test_scorecard_category_audience_is_permission_derived():
    """The audience mirrors the PERMISSION matrix (the SSOT), not a
    hand-picked role list — hr and dispatcher hold can_scorecard_* in
    the static defaults, so they ARE in the audience."""
    cat = get_category("alert.scorecard")
    assert cat is not None
    for role in ("safety", "fleet", "hr", "dispatcher", "owner"):
        assert cat.audience(role), role
    assert not cat.audience("unknown_role")


# ── permission type-gate + company scope (SSOT guards) ──────────────

class _Perms:
    """FeatureSet-shaped: attributes are the permission flags."""
    def __init__(self, **flags):
        for k, v in flags.items():
            setattr(self, k, v)


def test_board_type_gate_follows_effective_permissions():
    from capabilities.alerting.router import _filter_types_by_permission
    rows = [{"alert_type": "fault"}, {"alert_type": "maintenance"},
            {"alert_type": "documents"}, {"alert_type": "system"}]
    # A safety-ish set: faults yes, maintenance no, docs yes.
    user = {"_perms": _Perms(can_faults=True, can_manage_driver_docs=True)}
    kept = [r["alert_type"] for r in _filter_types_by_permission(user, rows)]
    assert kept == ["fault", "documents", "system"]   # system = untabled, fail-open
    # No stashed perms (legacy path) → nothing hidden.
    assert len(_filter_types_by_permission({}, rows)) == 4


@pytest.mark.asyncio
async def test_personal_fanout_carries_company_predicate(monkeypatch):
    dispatch = _Dispatch()

    async def _scope(account_id):
        return {7: ["G1"]}
    monkeypatch.setattr(
        "capabilities.alerting.company_scope.load_company_scope", _scope)
    monkeypatch.setattr(
        "capabilities.alerting.company_scope.user_sees_company",
        lambda uid, role, co, scope: not (uid == 7 and co == "G1"))
    posted, tenant, dispatch = await _post(
        monkeypatch, alert_type="maintenance", dispatch=dispatch,
        subject_id="v-105", subject_name="Truck 105",
        dedup_key="task:7", co="G1")
    rf = dispatch.calls[0].get("recipient_filter")
    assert rf is not None
    assert rf(7, "dispatcher") is False    # G1-restricted user dropped
    assert rf(8, "dispatcher") is True


# ── targeted alert notices (documents driver DM + shift report) ─────

def test_targeted_notice_categories_registered():
    from capabilities.notifications.categories import TARGETED
    doc = get_category("alert.document_expiry")
    assert doc is not None and doc.kind == TARGETED
    assert doc.audience("driver") and not doc.audience("dispatcher")
    rep = get_category("alert.shift_report")
    assert rep is not None and rep.kind == TARGETED
    assert rep.audience is None                   # any role can have one


@pytest.mark.asyncio
async def test_notify_user_accepts_the_targeted_notices():
    """notify_user rejects BROADCAST categories — the two notices must
    be TARGETED or the converted producers would crash at send time."""
    from capabilities.notifications.service import notify_user

    class _Db:
        async def get_notification_channel(self, *a):
            return None                           # nothing connected → no-op

    for cat in ("alert.document_expiry", "alert.shift_report"):
        from capabilities.notifications.channels import NotificationContent
        results = await notify_user(
            _Db(), 1, 42,
            NotificationContent(title="", body="x", category=cat),
            channels=("telegram_dm",))
        assert results == []                      # no raise, no connection
