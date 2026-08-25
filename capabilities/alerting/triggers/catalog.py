"""The metric vocabulary — what a person is allowed to watch.

This catalog is the whole reason alert triggers cost no feature code: a
trigger names a metric KEY, and everything else about that metric — where
the number lives, which direction is even meaningful, how noisy it is,
how fast it needs watching, when a reading may be trusted — is declared
here, once.  Adding "watch coolant temperature" is a line in this file.

It is also a WHITELIST.  The API never accepts a column name from a
caller; it accepts a key that must already be here.  Free-form columns
would turn a user-facing form into an arbitrary read of the warehouse.

Four facts every metric declares, and why each one is not the user's to
choose:

``direction``
    Pinned per metric.  Fuel, DEF, battery and oil only ever matter on
    the way DOWN; coolant only on the way UP.  Letting someone ask for
    "fuel ABOVE 26%" would be a control that produces nothing.

``hysteresis``
    ABSOLUTE, never a percentage of the chosen value: 5% of 12.6 V and
    5% of 190 °C are different physics, and a percentage collapses to
    nothing near zero.  A crossing re-arms only after the reading
    recovers past ``value ± hysteresis``, which is what stops a metric
    hovering on the line from alerting all day.

``stale_after``
    Per metric, because "old" means different things.  A fuel reading is
    still worth acting on hours later; an oil-pressure reading from an
    hour ago says nothing about now.  Beyond it, the vehicle is skipped
    in silence — a three-day-old 26% is not a fact, and the truck has
    probably refuelled.

``requires_engine``
    The one the production data insisted on.  Measured over 24 hours of
    this fleet: engine OFF averages 11.0 V and 14.4 psi; running averages
    13.8 V and 42 psi.  A resting battery reads low because nothing is
    charging it, and a stopped engine has no oil pressure — so a
    "battery below 12 V" trigger without this gate would fire on every
    parked truck every night.  Not a bug in the threshold; a category
    error about what the number means.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Every v1 metric reads the MINUTE tier.  Not the "live" tier, which
# sounds fresher and is not: measured across this fleet, minute held the
# fresher reading for 102 vehicles out of 102, while the live tier —
# upserted in place, so a departed device's last row sits there forever —
# carried rows nearly a month old wearing a current-looking face.  Minute
# also carries all five metrics; live carries two.
#
# Never read a metric through a service facade instead.  Some of them
# (``get_low_fuel_vehicles``) fall back to a live provider call when
# warehouse reads are disabled, which would put a scheduled sweep on the
# customer's Samsara quota.  The evaluator reads warehouse tables direct.
MINUTE = "vehicle_state_minute"

Direction = Literal["below", "above"]


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    column: str
    direction: Direction
    #: What a person may choose as their threshold.
    settable: tuple[float, float]
    #: What a READING must be inside to be believed.  Outside it the
    #: sample is a sensor dropout, not a breach — battery reports a flat
    #: 0.0 V often enough that a below-trigger would fire on every one.
    plausible: tuple[float, float]
    #: Absolute re-arm band (see module docstring).
    hysteresis: float
    #: A reading older than this is skipped in silence.
    stale_after_minutes: int
    #: How often this metric is worth re-reading.
    check_every_minutes: int
    #: ``"on"`` = only evaluate while the engine runs.
    requires_engine: str | None = None
    source: str = MINUTE
    #: Shown under the number in the editor — the sentence that stops
    #: someone setting a physically meaningless threshold.
    hint: str = ""


CATALOG: tuple[Metric, ...] = (
    Metric(
        key="fuel_pct", label="Fuel level", unit="%",
        column="fuel_pct", direction="below",
        settable=(5, 60), plausible=(0, 100),
        hysteresis=5, stale_after_minutes=24 * 60, check_every_minutes=15,
        hint="A tank level is meaningful parked or moving.",
    ),
    Metric(
        key="def_pct", label="DEF level", unit="%",
        column="def_pct", direction="below",
        settable=(5, 60), plausible=(0, 100),
        hysteresis=5, stale_after_minutes=24 * 60, check_every_minutes=15,
        hint="Running out derates the engine — worth catching early.",
    ),
    Metric(
        key="battery_v", label="Battery voltage", unit="V",
        column="battery_v", direction="below",
        settable=(11.0, 13.5), plausible=(6, 32),
        hysteresis=0.5, stale_after_minutes=60, check_every_minutes=5,
        requires_engine="on",
        hint="Charging-system voltage, engine running (~13.8 V healthy). "
             "A parked truck rests near 11 V with nothing charging it, "
             "which is normal and not an alert.",
    ),
    Metric(
        key="coolant_c", label="Coolant temperature", unit="°C",
        column="coolant_c", direction="above",
        settable=(95, 120), plausible=(-40, 150),
        hysteresis=5, stale_after_minutes=60, check_every_minutes=5,
        requires_engine="on",
        hint="Normal running range peaks near 90 °C.",
    ),
    Metric(
        key="oil_psi", label="Oil pressure", unit="psi",
        column="oil_psi", direction="below",
        settable=(5, 40), plausible=(0, 200),
        hysteresis=10, stale_after_minutes=60, check_every_minutes=5,
        requires_engine="on",
        hint="Engine running: idle sits near 30 psi, moving near 42. "
             "A stopped engine reads near zero, which is not a fault.",
    ),
)

_BY_KEY = {m.key: m for m in CATALOG}


def get_metric(key: str) -> Metric | None:
    """The metric, or None when the key is not in the whitelist."""
    return _BY_KEY.get(str(key or ""))


def metric_keys() -> tuple[str, ...]:
    return tuple(_BY_KEY)


def columns_needed(keys) -> list[str]:
    """The warehouse columns one sweep must read for these metric keys.

    Always includes what every metric needs to be judged at all:
    ``engine_state`` for the engine gate and ``source_ts`` for freshness.
    """
    cols = {"vehicle_id", "vehicle_name", "engine_state", "source_ts", "captured_at"}
    for k in keys:
        m = get_metric(k)
        if m is not None:
            cols.add(m.column)
    return sorted(cols)


def breaches(metric: Metric, reading: float, threshold: float) -> bool:
    """Is this reading in breach of the threshold, in the metric's own
    (pinned) direction?"""
    return reading < threshold if metric.direction == "below" else reading > threshold


def recovered(metric: Metric, reading: float, threshold: float) -> bool:
    """Has the reading recovered PAST the re-arm band — i.e. far enough
    the other way that a fresh crossing would mean something new?

    Deliberately not ``not breaches(...)``: a value sitting exactly on
    the threshold would then re-arm and re-fire on every sweep, which is
    the flapping this band exists to prevent.
    """
    if metric.direction == "below":
        return reading >= threshold + metric.hysteresis
    return reading <= threshold - metric.hysteresis


def reading_usable(metric: Metric, reading, engine_state: str) -> bool:
    """Whether this sample may be judged at all.

    Three ways a reading is not evidence: it is missing, it is outside
    the metric's plausible range (a sensor dropout — battery reports a
    flat 0.0 V), or the engine is off for a metric that only means
    something while it runs.
    """
    if reading is None:
        return False
    try:
        value = float(reading)
    except (TypeError, ValueError):
        return False
    lo, hi = metric.plausible
    if not (lo <= value <= hi):
        return False
    if metric.requires_engine == "on":
        # The warehouse's engine vocabulary is exactly moving/idle/off
        # (plus blank when unknown) — a blank is not a claim the engine
        # is running, so it fails closed with everything else.
        return str(engine_state or "").strip().lower() in ("idle", "moving")
    return True


def settable_error(metric: Metric, value: float) -> str:
    """'' when the chosen threshold is allowed, else why it is not."""
    lo, hi = metric.settable
    if not (lo <= value <= hi):
        return (f"{metric.label} triggers accept {lo}–{hi}{metric.unit}"
                f" — {value}{metric.unit} would "
                + ("never fire" if value < lo else "fire on almost every vehicle"))
    return ""
