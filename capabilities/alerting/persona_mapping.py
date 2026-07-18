"""Alert-type → persona routing table.

Single source of truth for which persona group receives which alert
when the account is in ``alert_routing_mode = 'per_persona_groups'``.

Persona buckets:

  • dispatcher → live-operations alerts (geofence, parking, fuel)
  • safety     → driver-behavior + media (events, camera)
  • fleet      → mechanical health + DTCs (faults, health, scorecard,
                  maintenance, samsara_sync)
  • hr         → driver-documentation expiries
  • owner_admin → cross-cutting (system, reescalate, billing-ish)

Owner / Admin do NOT get their own per-alert-type stream — they
subscribe to the aggregate group, which receives every CRITICAL alert
regardless of which persona group also got it.  The aggregate fanout
is handled in ``routing_resolver``; this table only decides the
*primary* persona target.

If an alert_type isn't in the map the resolver falls back to the
owner_admin aggregate — safe default since the owner can always
escalate, vs. dropping the alert silently.
"""

from __future__ import annotations

from typing import Final

OWNER_ADMIN: Final[str] = "owner_admin"
DISPATCHER: Final[str] = "dispatcher"
SAFETY: Final[str] = "safety"
FLEET: Final[str] = "fleet"
HR: Final[str] = "hr"

# Persona keys recognised by ``account_persona_groups.persona``.
# Listed for assertion-style validation in tests.
PERSONAS: Final[tuple[str, ...]] = (
    OWNER_ADMIN, DISPATCHER, SAFETY, FLEET, HR,
)

# Alert-type aliases are deliberately permissive — the pipeline
# accepts both ``fault``/``faults``, ``event``/``events``,
# ``doc_expiry``/``documents``.  Mirror that here so the resolver
# doesn't have to canonicalise upstream.
_ALERT_TYPE_TO_PERSONA: dict[str, str] = {
    # Dispatcher — live operations
    "geofence":     DISPATCHER,
    "parking":      DISPATCHER,
    "fuel":         DISPATCHER,
    # Safety — driver behavior + media
    "events":       SAFETY,
    "event":        SAFETY,
    "camera":       SAFETY,
    # Fleet — mechanical
    "faults":       FLEET,
    "fault":        FLEET,
    "health":       FLEET,
    "scorecard":    FLEET,
    "maintenance":  FLEET,
    "samsara_sync": FLEET,
    # HR — documents
    "documents":    HR,
    "doc_expiry":   HR,
    # Owner / Admin aggregate — cross-cutting
    "system":       OWNER_ADMIN,
    "reescalate":   OWNER_ADMIN,
}


# Pipeline-side aliases → the canonical ``ALERT_TYPE_KEYS`` slug the
# settings layer keys on (``forum_ai.{key}`` / ``persona_route.{key}``).
_CANONICAL_ALIASES: dict[str, str] = {
    "fault":        "faults",
    "event":        "events",
    "doc_expiry":   "documents",
    "samsara_sync": "system",
    "reescalate":   "system",
}


def canonical_route_key(alert_type: str) -> str:
    """Normalise a pipeline alert_type to its canonical settings slug."""
    return _CANONICAL_ALIASES.get(alert_type, alert_type)


def canonical_types_for_persona(persona: str) -> list[str]:
    """The canonical ALERT_TYPE_KEYS slugs routed to *persona* by the
    STATIC default map — the seed grouping only.  The settings UI and
    the fan-out resolver use :func:`types_for_role` instead, which
    derives each role's types from the account's PERMISSION matrix
    (the single source of truth)."""
    from adapters.storage.models import ALERT_TYPE_KEYS
    return [k for k in ALERT_TYPE_KEYS if persona_for_alert(k) == persona]


# ── Permission-driven topics (the matrix is the SSOT) ────────────────
# Which FEATURE permission grants a role each alert type.  When the
# owner grants a role a feature in the Permissions matrix (e.g. Fleet
# gets Safety Events), that role's group topics — and the alert
# fan-out — follow automatically.  any-of semantics per type.
ALERT_TYPE_FEATURE_FLAGS: Final[dict[str, tuple[str, ...]]] = {
    "faults":      ("can_faults",),
    "health":      ("can_health",),
    "fuel":        ("can_fuel",),
    "events":      ("can_events_all", "can_events_vehicle"),
    "camera":      ("can_cameras",),
    "parking":     ("can_parking_all", "can_parking_vehicle"),
    "geofence":    ("can_geofence_all", "can_geofence_vehicle"),
    "scorecard":   ("can_scorecard_all", "can_scorecard_vehicle"),
    "maintenance": ("can_maintenance_all", "can_maintenance_vehicle"),
    "documents":   ("can_manage_driver_docs",),
    "system":      ("can_manage_integrations", "can_manage_account"),
}

# Staff roles that can hold a group row on the bot roster.  Drivers get
# DMs, never a staff group; owner/admin live on the Main row.
STAFF_ROLES: Final[tuple[str, ...]] = (
    "dispatcher", "safety", "fleet", "hr", "accounting", "recruiter",
)


async def types_for_role(account_id: int, role: str) -> list[str]:
    """The alert types *role* may route to its group in THIS account —
    derived from the effective per-account permission set (matrix
    overrides included), not the static map.  Unknown role → []."""
    from adapters.storage.models import ALERT_TYPE_KEYS, Role
    from capabilities.permissions.roles import get_account_permissions
    try:
        perms = await get_account_permissions(Role(role), account_id, None)
    except Exception:
        return []
    return [
        k for k in ALERT_TYPE_KEYS
        if any(getattr(perms, f, False) for f in ALERT_TYPE_FEATURE_FLAGS[k])
    ]


def persona_for_alert(alert_type: str) -> str:
    """Return the persona key that should receive ``alert_type``.

    Unknown alert_types fall back to ``owner_admin`` — safer than
    dropping the alert.  Callers can detect this case by comparing the
    return value to OWNER_ADMIN if they need a different default.
    """
    return _ALERT_TYPE_TO_PERSONA.get(alert_type, OWNER_ADMIN)


def all_alert_types() -> tuple[str, ...]:
    """Every alert_type the mapping knows about, for test coverage."""
    return tuple(_ALERT_TYPE_TO_PERSONA.keys())


# Inverse view of ``_ALERT_TYPE_TO_PERSONA`` — for a given persona key,
# return the set of alert_types whose primary routing target is that
# persona.  Used by the Overview "Pending alerts" KPI to filter the
# account-wide count down to alerts an operator actually triages, so
# a Safety user doesn't see fault-and-fuel counts inflating their
# "needs attention" number.
#
# Computed lazily at module import — cheap (tiny dict), and the
# mapping is static for the process lifetime.
def _build_persona_to_alert_types() -> dict[str, frozenset[str]]:
    inverse: dict[str, set[str]] = {p: set() for p in PERSONAS}
    for alert_type, persona in _ALERT_TYPE_TO_PERSONA.items():
        inverse.setdefault(persona, set()).add(alert_type)
    return {p: frozenset(s) for p, s in inverse.items()}


_PERSONA_TO_ALERT_TYPES: dict[str, frozenset[str]] = _build_persona_to_alert_types()


def alert_types_for_persona(persona: str) -> frozenset[str]:
    """Every alert_type whose primary routing target is ``persona``.

    Unknown persona → empty set (caller should treat as "no filter",
    not "drop everything").  ``owner_admin`` returns only the cross-
    cutting types directly mapped to it (system, reescalate); owner
    role itself sees ALL alerts via the explicit cross-cutting flag
    callers use (see ``role_sees_all_alerts``).
    """
    return _PERSONA_TO_ALERT_TYPES.get(persona, frozenset())


# Role string → persona key.  Mirrors capabilities.alerting.on_shift's
# role→persona table but lives here so the storage / API layer can
# import without pulling on_shift's DB-touching helpers.
_ROLE_TO_PERSONA: dict[str, str] = {
    "owner":      OWNER_ADMIN,
    "admin":      OWNER_ADMIN,
    "dispatcher": DISPATCHER,
    "safety":     SAFETY,
    "fleet":      FLEET,
    "hr":         HR,
}


def alert_types_for_role(role: str) -> frozenset[str]:
    """The alert_types an operator in ``role`` should see counted on
    persona-aware dashboards (Overview KPI, persona summary strips).

    Strict binding (no cross-cutting bypass):
      • owner / admin → only the OWNER_ADMIN persona's types
        (``system``, ``reescalate``).  These are the
        cross-cutting *digest* tokens — not faults/parking/etc.
        Owner / Admin's executive view is powered by
        EscalationStatusCard + AccountAlertSummary, NOT by the
        operational pending_alerts KPI.
      • dispatcher / safety / fleet / hr → their own persona's types
      • accounting / driver / unknown → empty (no operational
        pending-alerts surface; accounting uses cost reports,
        driver is vehicle-scoped via a different code path).

    Callers that previously bypassed the filter for owner/admin via
    ``role_sees_all_alerts`` should treat empty/strict-bound results
    as the authoritative scope.  Owner / Admin who want the
    account-wide view click "View dashboard as Fleet" → the
    SPA sends ``X-View-As: fleet`` → backend resolves to Fleet's
    alert types.
    """
    persona = _ROLE_TO_PERSONA.get(role)
    if persona is None:
        return frozenset()
    return _PERSONA_TO_ALERT_TYPES.get(persona, frozenset())
