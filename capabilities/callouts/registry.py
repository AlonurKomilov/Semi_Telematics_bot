"""The callout key vocabulary — one owner, so keys cannot drift.

Without this, one feature emits ``vehicle.no_engine_data`` and the
next writes ``vehicles.noEngineData``, and nothing catches it until a
user sees a raw key on screen.  Every key a feature can emit is
declared here at import time, exactly as the retention hub has each
feature declare its targets.

What this registry deliberately does NOT have yet is ``discover()``.
That machinery exists in the data-lifecycle hubs so a SCHEDULER can
fan out over every contributor; nothing enumerates callouts, because
each feature's module is already imported by the code that emits its
callouts.  The day an account-wide "what needs attention" digest wants
the full set, ``make_discover`` is a ten-line addition here.

Kinds are not decoration — each carries a different dismissal
lifecycle, which is the whole reason it has its own name:

    caveat     qualifies the data on screen — NEVER dismissible,
               because hiding it re-hides the thing it corrects.
    condition  a state that clears when the world changes, not when
               the reader clicks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

KINDS = ("caveat", "condition")

# ``<feature>.<name>`` — enforced, not merely conventional.  A callout's
# public identity (``callout_id``) is built from its key, so two
# features minting the same bare key would mint the SAME dismissal id
# for unrelated faults: silencing one would silence the other.  The
# namespace is what makes ids collision-free across every feature and
# integration that ever emits a callout.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
SEVERITIES = ("info", "warn", "danger")


@dataclass(frozen=True)
class CalloutSpec:
    """One declared key.  ``owner`` is the feature that emits it —
    recorded so a stray key traces back to a module, not a grep."""

    key: str
    kind: str
    severity: str
    owner: str


# test-safe: declared per feature at import; tests read the specs.
_REGISTRY: dict[str, CalloutSpec] = {}


def register_callout(
    key: str, *, kind: str, severity: str, owner: str,
) -> CalloutSpec:
    """Declare a key.  Re-registering the SAME shape is a no-op so a
    module imported twice is harmless; re-registering a DIFFERENT shape
    raises, because that means two features are fighting over one key.
    """
    if not KEY_RE.match(key or ""):
        raise ValueError(
            f"callout key {key!r} must be '<feature>.<name>' "
            f"(lowercase, digits, underscores) — the namespace is what "
            f"keeps dismissal ids from colliding across features"
        )
    if kind not in KINDS:
        raise ValueError(f"callout {key!r}: unknown kind {kind!r} (of {KINDS})")
    if severity not in SEVERITIES:
        raise ValueError(
            f"callout {key!r}: unknown severity {severity!r} (of {SEVERITIES})"
        )
    spec = CalloutSpec(key=key, kind=kind, severity=severity, owner=owner)
    prior = _REGISTRY.get(key)
    if prior is not None and prior != spec:
        raise ValueError(
            f"callout key {key!r} already declared by {prior.owner!r} "
            f"as {prior.kind}/{prior.severity}; {owner!r} wants "
            f"{kind}/{severity}"
        )
    _REGISTRY[key] = spec
    return spec


def get_spec(key: str) -> CalloutSpec | None:
    return _REGISTRY.get(key)


def known_keys() -> tuple[str, ...]:
    """Every declared key, sorted — the drift guard compares this with
    the dashboard's ``calloutCatalog.ts`` so a key the backend can emit
    can always be rendered."""
    return tuple(sorted(_REGISTRY))


# ── Condition detectors ──────────────────────────────────────────
#
# A capability may not import a feature, but the ingest tick — which
# lives in a capability — is the cheapest place to notice that a
# vehicle's condition is true: it already holds the row.  So the
# feature REGISTERS how to decide, and the capability calls through
# here without ever naming it.  Exactly the arrangement the
# data-lifecycle hubs use, and the reason ``discover()`` exists.
#
# A detector answers one question about one row:
#     (row, prior) -> params dict when the condition holds, else None
# ``params`` becomes the callout's render substitutions, so returning
# ``{}`` means "true, nothing to substitute" — distinct from ``None``.

DetectFn = Callable[[dict, dict], "dict[str, Any] | None"]


@dataclass(frozen=True)
class ConditionDetector:
    key: str
    detect: DetectFn


# test-safe: detectors are declared beside their specs at import; tests read them.
_DETECTORS: dict[str, ConditionDetector] = {}


def register_detector(key: str, detect: DetectFn) -> None:
    """Declare how to decide one condition.  The key must already be
    registered — a detector for an unknown callout would open rows
    nothing can ever render."""
    if key not in _REGISTRY:
        raise ValueError(
            f"detector for unregistered callout {key!r} — call "
            f"register_callout first"
        )
    _DETECTORS[key] = ConditionDetector(key=key, detect=detect)


def condition_detectors() -> tuple[ConditionDetector, ...]:
    """Every registered detector.  Call ``discover()`` first."""
    return tuple(_DETECTORS.values())
