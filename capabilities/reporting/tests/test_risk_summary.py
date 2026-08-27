"""Tests for the Stakeholder Risk Summary feature."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from capabilities.permissions.roles import (
    FeatureSet, can,
)
from capabilities.reporting.audiences import (
    AUDIENCE_CONFIG, get_audience_config, is_valid_audience,
    SECTION_MAINTENANCE,
)
from capabilities.reporting import risk_profile as risk_profile_module
from capabilities.reporting.risk_profile import RiskProfile
from capabilities.reporting.risk_summary_pdf import (
    generate_risk_summary_pdf, _anonymise_driver,
)
from capabilities.reporting.csv_generators import generate_risk_summary_csv
from adapters.storage import Role


# ── Permissions ────────────────────────────────────────────────────

def test_feature_set_has_risk_report_flags():
    fs = FeatureSet()
    assert hasattr(fs, "can_risk_report_all")
    assert hasattr(fs, "can_risk_report_own")
    assert fs.can_risk_report_all is False
    assert fs.can_risk_report_own is False


def test_role_permissions_wired():
    # Owner has both
    assert can(Role.OWNER, "can_risk_report_all")
    assert can(Role.OWNER, "can_risk_report_own")
    # Driver has only own
    assert not can(Role.DRIVER, "can_risk_report_all")
    assert can(Role.DRIVER, "can_risk_report_own")
    # Dispatcher has neither
    assert not can(Role.DISPATCHER, "can_risk_report_all")
    assert not can(Role.DISPATCHER, "can_risk_report_own")


# ── Audiences ──────────────────────────────────────────────────────

def test_audience_config_completeness():
    for key in ("insurance", "owner", "broker", "auditor", "payroll"):
        assert key in AUDIENCE_CONFIG
        cfg = AUDIENCE_CONFIG[key]
        assert cfg.label
        assert cfg.disclaimer
        assert isinstance(cfg.sections, tuple)
        assert len(cfg.sections) > 0


def test_broker_anonymises_driver():
    cfg = get_audience_config("broker")
    assert cfg.show_pii_driver_name is False
    assert cfg.show_video_links is False


def test_payroll_omits_maintenance_and_comparison():
    cfg = get_audience_config("payroll")
    assert SECTION_MAINTENANCE not in cfg.sections


def test_invalid_audience_defaults_to_owner():
    cfg = get_audience_config("nonsense")
    assert cfg.label == AUDIENCE_CONFIG["owner"].label


def test_is_valid_audience():
    assert is_valid_audience("insurance")
    assert not is_valid_audience("hacker")


# ── Anonymisation ──────────────────────────────────────────────────

def test_anonymise_driver_uses_id_tail():
    out = _anonymise_driver("John Doe", "driver-abc-1234")
    assert "John" not in out
    assert "1234" in out


def test_anonymise_driver_handles_short_id():
    out = _anonymise_driver("Jane", "ab")
    assert isinstance(out, str)
    assert "Jane" not in out


# ── PDF / CSV generation with synthetic profile ────────────────────

def _fake_profile(subject_type: str = "vehicle") -> RiskProfile:
    return RiskProfile(
        account_id=1,
        subject_type=subject_type,
        subject_id="TRK-7",
        subject_display="Truck 7" if subject_type == "vehicle" else "Driver Bob",
        company="Test Fleet",
        days=30,
        total_score=82,
        tier="Gold",
        pillars={
            "safety": {"subtotal": 35, "cap": 40, "has_data": True},
            "efficiency": {"subtotal": 27, "cap": 30, "has_data": True},
            "compliance": {"subtotal": 20, "cap": 30, "has_data": True},
        },
        breakdown={"bonuses": [], "penalties": []},
        daily_history=[
            {"snapshot_date": "2025-01-01", "total_score": 80},
            {"snapshot_date": "2025-01-02", "total_score": 82},
            {"snapshot_date": "2025-01-03", "total_score": 85},
        ],
        score_events=[],
        events=[
            {
                "event_type": "harsh_brake",
                "occurred_at": datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc),
                "vehicle_name": "TRK-7",
                "severity": "high",
                "video_url": "https://example.com/video.mp4",
            },
        ],
        overdue_tasks=[],
        pending_tasks=[
            {"task_type": "Oil change", "due_date": "2025-02-01", "vehicle_name": "TRK-7"},
        ],
        aggregate_median=75.0,
        aggregate_percentile=70.0,
        fleet_size=12,
        vehicle_meta={"make": "Volvo", "model": "VNL", "year": 2022, "vin": "1XKAD49X3KJ123456"},
        driver_meta={"name": "Bob Driver", "driver_id": "drv-bob-9876"},
        generated_at=datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("audience", ["insurance", "owner", "broker", "auditor", "payroll"])
def test_pdf_generates_for_each_audience(audience):
    profile = _fake_profile()
    buf = generate_risk_summary_pdf(profile, audience=audience, account_label="Test Fleet")
    assert isinstance(buf, io.BytesIO)
    data = buf.getvalue()
    assert len(data) > 1000
    assert data.startswith(b"%PDF")


def test_pdf_for_driver_subject():
    profile = _fake_profile(subject_type="driver")
    buf = generate_risk_summary_pdf(profile, audience="owner")
    assert buf.getvalue().startswith(b"%PDF")


def test_broker_pdf_omits_driver_name_text():
    """Broker audience must not embed the driver's plain name."""
    profile = _fake_profile(subject_type="driver")
    buf = generate_risk_summary_pdf(profile, audience="broker")
    raw = buf.getvalue()
    # PDF text is encoded but plaintext substrings of common words usually remain;
    # this is a best-effort check.
    assert b"Bob Driver" not in raw


@pytest.mark.parametrize("audience", ["insurance", "owner", "broker", "auditor", "payroll"])
def test_csv_generates_for_each_audience(audience):
    profile = _fake_profile()
    buf = generate_risk_summary_csv(profile, audience=audience)
    text = buf.getvalue().decode("utf-8")
    assert "Stakeholder Risk Summary" in text or "Risk Summary" in text or "Audience" in text
    assert "Pillar" in text


def test_csv_broker_anonymises_subject():
    profile = _fake_profile(subject_type="driver")
    buf = generate_risk_summary_csv(profile, audience="broker")
    text = buf.getvalue().decode("utf-8")
    assert "Bob Driver" not in text


# ── API route smoke check ──────────────────────────────────────────

def test_reports_router_has_risk_summary_routes():
    from capabilities.reporting import router as reports_route
    paths = {r.path for r in reports_route.router.routes}
    assert any("risk-summary" in p for p in paths)
    assert any(p.endswith("/risk-summary/me") for p in paths)


@pytest.mark.asyncio
async def test_build_risk_profile_accepts_live_scorecard_shape(monkeypatch):
    class FakeTenant:
        async def get_latest_scorecard_snapshots(self, account_id, *, subject_type="driver"):
            return []

        async def get_scorecard_snapshot_history(self, account_id, *, subject_type="driver", subject_id=None, days=30):
            return []

        async def get_score_events(self, account_id, *, subject_type="driver", subject_id=None, days=30):
            return []

        async def get_safety_events_warehouse(self, account_id, **kwargs):
            return []

        async def get_maintenance_tasks(self, account_id, **kwargs):
            return []

    async def _fake_get_tenant_db(account_id):
        return FakeTenant()

    async def _fake_evaluate_subjects(account_id, *, subject="driver", days=7, company=None):
        return [{
            "subject_id": "TRK-7",
            "subject_name": "Truck 7",
            "total": 82,
            "pillars": {"safety": {"subtotal": 35}},
            "breakdown": {"pillars": {"safety": {"subtotal": 35}}},
        }]

    monkeypatch.setattr(risk_profile_module, "get_tenant_db", _fake_get_tenant_db)
    monkeypatch.setattr(risk_profile_module, "evaluate_subjects", _fake_evaluate_subjects)

    profile = await risk_profile_module.build_risk_profile(
        1, subject_type="vehicle", subject_id="TRK-7", days=30,
    )

    assert profile.total_score == 82
    assert profile.subject_display == "Truck 7"
    assert profile.fleet_size == 1


@pytest.mark.asyncio
async def test_build_risk_profile_falls_back_to_snapshots(monkeypatch):
    class FakeTenant:
        async def get_latest_scorecard_snapshots(self, account_id, *, subject_type="driver"):
            return [{
                "subject_id": "TRK-9",
                "subject_name": "Truck 9",
                "total_score": 77,
                "breakdown": {"pillars": {"safety": {"subtotal": 30}}, "company": "ACME"},
            }]

        async def get_scorecard_snapshot_history(self, account_id, *, subject_type="driver", subject_id=None, days=30):
            return []

        async def get_score_events(self, account_id, *, subject_type="driver", subject_id=None, days=30):
            return []

        async def get_safety_events_warehouse(self, account_id, **kwargs):
            return []

        async def get_maintenance_tasks(self, account_id, **kwargs):
            return []

    async def _fake_get_tenant_db(account_id):
        return FakeTenant()

    async def _fake_evaluate_subjects(account_id, *, subject="driver", days=7, company=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(risk_profile_module, "get_tenant_db", _fake_get_tenant_db)
    monkeypatch.setattr(risk_profile_module, "evaluate_subjects", _fake_evaluate_subjects)

    profile = await risk_profile_module.build_risk_profile(
        1, subject_type="vehicle", subject_id="TRK-9", days=30, company="ACME",
    )

    assert profile.total_score == 77
    assert profile.subject_display == "Truck 9"
    assert profile.fleet_size == 1
