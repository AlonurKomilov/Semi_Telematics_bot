"""Stakeholder Risk Summary — audience configurations.

This module defines the *universal* audience framework that powers the
Stakeholder Risk Summary report.  The report itself is single-purpose
(per-subject risk profile) but its presentation, language, disclaimers,
and the set of sections rendered vary by intended reader.

Audiences supported:
    - insurance : underwriter / broker requesting telematics evidence
    - owner     : fleet owner internal review
    - broker    : broker-of-record packet (subset of insurance)
    - auditor   : DOT / compliance auditor
    - payroll   : safety-incentive / pay-by-score evidence

Adding a new audience requires only one entry in ``AUDIENCE_CONFIG``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Public type alias — keep in sync with AUDIENCE_CONFIG keys.
Audience = Literal["insurance", "owner", "broker", "auditor", "payroll"]


@dataclass(frozen=True)
class AudienceConfig:
    """Per-audience presentation + content rules.

    Sections are referenced by stable string IDs so the PDF generator
    can toggle them with a single ``id in cfg.sections`` check.
    """
    label: str                          # human-readable header subtitle
    sections: tuple[str, ...]           # ordered section IDs to render
    disclaimer: str                     # bold disclaimer printed in footer
    show_video_links: bool = True       # safety-event clip URLs
    show_methodology: bool = True       # always on by default (regulatory)
    show_pii_driver_name: bool = True   # set False for broker-anonymised packets
    accent_label: str = "Risk Summary"  # cover-page banner subtitle


# Stable section IDs (do NOT rename without updating the PDF generator).
SECTION_COVER         = "cover"
SECTION_SCORE_GAUGE   = "score_gauge"
SECTION_PILLARS       = "pillars"
SECTION_TREND         = "trend"
SECTION_INCIDENTS     = "incidents"
SECTION_MAINTENANCE   = "maintenance"
SECTION_COMPARISON    = "comparison"
SECTION_VEHICLE_INFO  = "vehicle_info"
SECTION_DRIVER_INFO   = "driver_info"
SECTION_METHODOLOGY   = "methodology"
SECTION_DATA_SOURCES  = "data_sources"


_DEFAULT_SECTIONS: tuple[str, ...] = (
    SECTION_COVER,
    SECTION_VEHICLE_INFO,
    SECTION_DRIVER_INFO,
    SECTION_SCORE_GAUGE,
    SECTION_PILLARS,
    SECTION_TREND,
    SECTION_INCIDENTS,
    SECTION_MAINTENANCE,
    SECTION_COMPARISON,
    SECTION_METHODOLOGY,
    SECTION_DATA_SOURCES,
)


AUDIENCE_CONFIG: dict[str, AudienceConfig] = {
    "insurance": AudienceConfig(
        label="Insurance Underwriter Packet",
        sections=_DEFAULT_SECTIONS,
        disclaimer=(
            "This report is prepared for insurance underwriting review. "
            "All telemetry is sourced from the carrier's Samsara ELD/AOBRD "
            "feed and is provided AS-IS without warranty. Scores are "
            "operational risk indicators, not actuarial loss predictions."
        ),
        accent_label="Insurance Risk Summary",
    ),
    "owner": AudienceConfig(
        label="Fleet Owner Internal Review",
        sections=_DEFAULT_SECTIONS,
        disclaimer=(
            "Internal report for fleet management review. "
            "Contains driver-identifiable information — handle per "
            "your privacy and HR policies."
        ),
        accent_label="Fleet Owner Risk Summary",
    ),
    "broker": AudienceConfig(
        label="Broker of Record Packet",
        sections=(
            SECTION_COVER,
            SECTION_VEHICLE_INFO,
            SECTION_SCORE_GAUGE,
            SECTION_PILLARS,
            SECTION_INCIDENTS,
            SECTION_COMPARISON,
            SECTION_METHODOLOGY,
            SECTION_DATA_SOURCES,
        ),
        disclaimer=(
            "Prepared for broker-of-record submission. Driver names are "
            "redacted to anonymised IDs in keeping with broker data-sharing "
            "norms. Re-identification requires the carrier's written consent."
        ),
        show_pii_driver_name=False,
        show_video_links=False,
        accent_label="Broker Risk Summary",
    ),
    "auditor": AudienceConfig(
        label="DOT / Compliance Auditor Packet",
        sections=(
            SECTION_COVER,
            SECTION_VEHICLE_INFO,
            SECTION_DRIVER_INFO,
            SECTION_SCORE_GAUGE,
            SECTION_PILLARS,
            SECTION_TREND,
            SECTION_INCIDENTS,
            SECTION_MAINTENANCE,
            SECTION_METHODOLOGY,
            SECTION_DATA_SOURCES,
        ),
        disclaimer=(
            "Prepared for compliance / audit review. Underlying telemetry "
            "is retained per DOT record-keeping requirements and is "
            "available on request."
        ),
        accent_label="Compliance Audit Packet",
    ),
    "payroll": AudienceConfig(
        label="Safety-Pay Evidence Packet",
        sections=(
            SECTION_COVER,
            SECTION_DRIVER_INFO,
            SECTION_SCORE_GAUGE,
            SECTION_PILLARS,
            SECTION_TREND,
            SECTION_INCIDENTS,
            SECTION_METHODOLOGY,
        ),
        disclaimer=(
            "This report supports safety-bonus / pay-by-score programs. "
            "Driver-facing scores are provided for transparency — disputes "
            "should be raised with the safety manager within 14 days."
        ),
        show_video_links=False,
        accent_label="Driver Safety-Pay Summary",
    ),
}


def get_audience_config(audience: str) -> AudienceConfig:
    """Resolve an audience string to its config; defaults to ``owner``."""
    cfg = AUDIENCE_CONFIG.get(audience)
    if cfg is None:
        return AUDIENCE_CONFIG["owner"]
    return cfg


def is_valid_audience(audience: str) -> bool:
    return audience in AUDIENCE_CONFIG
