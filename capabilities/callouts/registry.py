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
    guidance   an optional suggestion — dismissible and remembered.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("caveat", "condition", "guidance")
SEVERITIES = ("info", "warn", "danger")


@dataclass(frozen=True)
class CalloutSpec:
    """One declared key.  ``owner`` is the feature that emits it —
    recorded so a stray key traces back to a module, not a grep."""

    key: str
    kind: str
    severity: str
    owner: str


_REGISTRY: dict[str, CalloutSpec] = {}


def register_callout(
    key: str, *, kind: str, severity: str, owner: str,
) -> CalloutSpec:
    """Declare a key.  Re-registering the SAME shape is a no-op so a
    module imported twice is harmless; re-registering a DIFFERENT shape
    raises, because that means two features are fighting over one key.
    """
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
