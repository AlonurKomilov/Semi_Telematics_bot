"""Cross-system identity drift detector for the Driver Module.

Three classes of mismatch the daily sync job watches for:

  ``orphaned``    Local ``users.samsara_driver_id`` points at a driver
                  that no longer exists in Samsara (was deleted /
                  replaced).  Action: admin re-picks from the dropdown.

  ``name_drift``  Local driver IS linked, but the Samsara name doesn't
                  match ``users.display_name`` (case-insensitive, after
                  whitespace strip).  Action: visual review — usually
                  one side has a typo or a marriage / nickname change.

  ``suggest``     Samsara has a static driver assignment for a truck
                  whose local driver ``users.truck_num`` matches, but
                  ``users.samsara_driver_id`` is blank.  Action: open
                  Drivers page and link.

The detector is a pure function — the bot wrapper feeds it the three
lists (local drivers, Samsara drivers, static assignments) and the
helper returns the mismatch list with a one-line summary per row.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mismatch:
    kind:         str           # 'orphaned' | 'name_drift' | 'suggest'
    user_id:      int | None    # Local users.id (None for ``suggest`` if no local row)
    display_name: str           # Local name or Samsara name (best available)
    truck_num:    str | None    # Vehicle for context (None if unassigned)
    samsara_id:   str | None    # Linked or candidate Samsara driver ID
    samsara_name: str | None    # Name in Samsara
    note:         str           # One-line human-readable summary


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def detect_mismatches(
    local_drivers: list,
    samsara_drivers: list[dict],
    static_assignments: dict[str, str],
) -> list[Mismatch]:
    """Compute the drift list.  All inputs come from already-fetched
    data; this function is pure so unit tests can feed synthetic
    inputs without touching DB or Samsara.

    Args:
        local_drivers: ``DriverProfile`` objects from ``list_drivers``.
            Only fields read: ``user_id``, ``display_name``, ``truck_num``,
            ``samsara_driver_id``.
        samsara_drivers: raw Samsara driver dicts.  Reads ``id`` and
            ``name`` (already tagged with ``_org`` by MultiCompanyClient).
        static_assignments: ``{vehicle_name_lower: driver_name}`` from
            ``SamsaraClient.get_static_driver_assignments``.
    """
    samsara_by_id: dict[str, dict] = {
        str(d.get("id", "") or ""): d for d in samsara_drivers
    }
    samsara_by_name: dict[str, str] = {
        _norm(d.get("name")): str(d.get("id", "") or "")
        for d in samsara_drivers if d.get("name") and d.get("id")
    }

    out: list[Mismatch] = []

    for drv in local_drivers:
        sid = (getattr(drv, "samsara_driver_id", None) or "").strip()
        local_name = getattr(drv, "display_name", "") or ""
        truck_num = getattr(drv, "truck_num", None)

        if sid:
            # Linked — check orphan / name drift.
            samsara_record = samsara_by_id.get(sid)
            if samsara_record is None:
                out.append(Mismatch(
                    kind="orphaned",
                    user_id=drv.user_id,
                    display_name=local_name,
                    truck_num=truck_num,
                    samsara_id=sid,
                    samsara_name=None,
                    note=(
                        f"Linked to Samsara ID {sid}, but that driver "
                        f"no longer exists in the fleet."
                    ),
                ))
                continue
            s_name = samsara_record.get("name") or ""
            if _norm(s_name) != _norm(local_name):
                out.append(Mismatch(
                    kind="name_drift",
                    user_id=drv.user_id,
                    display_name=local_name,
                    truck_num=truck_num,
                    samsara_id=sid,
                    samsara_name=s_name,
                    note=(
                        f"Local name '{local_name}' doesn't match "
                        f"Samsara name '{s_name}'."
                    ),
                ))
            continue

        # Unlinked — suggest if Samsara has a static driver assigned to
        # this local driver's truck.
        if not truck_num:
            continue
        suggested_name = static_assignments.get(_norm(truck_num))
        if not suggested_name:
            continue
        suggested_id = samsara_by_name.get(_norm(suggested_name))
        out.append(Mismatch(
            kind="suggest",
            user_id=drv.user_id,
            display_name=local_name,
            truck_num=truck_num,
            samsara_id=suggested_id,
            samsara_name=suggested_name,
            note=(
                f"Truck #{truck_num} is driven by '{suggested_name}' in "
                f"Samsara — link the Samsara ID from the Drivers page."
            ),
        ))

    return out


def format_digest(mismatches: list[Mismatch]) -> str:
    """Render a single Telegram-HTML digest message for admins.

    Groups by kind so admins can triage by severity: orphaned (loud)
    first, then name_drift, then suggest.  Capped at 25 lines per kind
    so a busy fleet doesn't produce a wall of text.
    """
    if not mismatches:
        return ""

    order = ("orphaned", "name_drift", "suggest")
    labels = {
        "orphaned":   ("🟥", "Orphaned Samsara links"),
        "name_drift": ("🟧", "Name mismatches"),
        "suggest":    ("💡", "Suggested links"),
    }

    by_kind: dict[str, list[Mismatch]] = {k: [] for k in order}
    for m in mismatches:
        by_kind.setdefault(m.kind, []).append(m)

    lines: list[str] = [
        "🔄 <b>Driver / Samsara sync</b>",
        f"  {len(mismatches)} mismatch(es) found across the fleet.",
        "",
    ]
    for kind in order:
        items = by_kind.get(kind) or []
        if not items:
            continue
        icon, label = labels[kind]
        lines.append(f"{icon} <b>{label}</b> ({len(items)})")
        for m in items[:25]:
            who = m.display_name or f"User #{m.user_id}"
            truck = f" — Truck #{m.truck_num}" if m.truck_num else ""
            lines.append(f"  • {who}{truck}")
            lines.append(f"    {m.note}")
        if len(items) > 25:
            lines.append(f"  …and {len(items) - 25} more")
        lines.append("")
    lines.append("Open the Drivers page on the dashboard to fix.")
    return "\n".join(lines)
