"""Driver-profile + vehicle-assignment + document-store mixins.

Drivers stay a special role within the ``users`` table — they are
not a separate entity.  These mixins read/write the driver-specific
columns on ``users`` plus the two new tables introduced by the
driver-module migrations:

  * ``driver_vehicle_assignments`` — single source of truth for who
    drives what, with history preserved via ``unassigned_at``.
  * ``driver_documents``           — per-driver document store with
    expiration tracking; files live in the existing ``ObjectStore``
    (Google Drive when the account has it linked, local disk
    fallback otherwise).

The local-disk fallback enforces a per-account quota
(``accounts.storage_quota_bytes``) so a single tenant can't fill
the volume on the host.  Google-Drive-connected accounts use their
own Drive quota and are uncapped here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from capabilities.integrations import reconciliation as recon


# Driver-profile columns containing personally-identifiable information.
# Stored encrypted at rest via ``infra.crypto`` (Fernet AES-GCM under the
# ENCRYPTION_KEY env var) and transparently decrypted on read.  Legacy
# rows that pre-date this rollout flow through ``decrypt`` unchanged —
# the function passes plaintext through when it doesn't see the ``enc::``
# prefix, so the mixed-state period during the backfill works correctly.
#
# Non-PII columns (cdl_state, cdl_class, *_expires, *_date, samsara_id)
# stay plaintext so DOT compliance reports and expiry-alert schedulers
# don't have to thread decryption through every aggregate query.
_PII_COLUMNS = frozenset({
    "cdl_number", "dob", "phone", "home_address", "driver_notes",
})


def _maybe_decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a stored PII value, tolerating None + legacy plaintext.

    Returns the input unchanged when it's None / empty / lacks the
    ``enc::`` marker — keeps the call sites short (no per-field None
    checks needed in ``_row_to_profile``).

    ``infra.crypto`` is imported lazily to dodge an import cycle:
    ``adapters/storage/__init__.py`` pulls every mixin including this
    one, and a top-level ``from infra...`` would trigger
    ``infra/__init__.py`` which in turn imports ``Database`` from
    ``adapters.storage`` — a partial-init crash.
    """
    if not value:
        return value
    # Lazy import — see docstring.
    from infra.crypto import decrypt as _crypto_decrypt
    try:
        return _crypto_decrypt(value)
    except ValueError as e:
        # Wrong key, corrupt ciphertext, etc.  We log and return a
        # placeholder so admin pages keep working even when one row
        # has gone bad.
        logger.warning("PII decrypt failed: %s", e)
        return "[encrypted]"


def _maybe_encrypt(value: Optional[str]) -> Optional[str]:
    """Encrypt a PII value before write, tolerating None.

    When ``ENCRYPTION_KEY`` is unset (dev fallback), ``encrypt`` is a
    pass-through and rows land in plaintext.  That keeps test fixtures
    that ship without a key working — the production deploy MUST set
    the key for actual at-rest protection.

    Lazy import same as ``_maybe_decrypt`` for the same reason.
    """
    if not value:
        return value
    from infra.crypto import encrypt as _crypto_encrypt
    return _crypto_encrypt(value)

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        def acquire(self) -> Any: ...
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
else:
    _MixinBase = object

logger = logging.getLogger(__name__)


# ── Dataclasses ────────────────────────────────────────────────


@dataclass
class DriverProfile:
    """The driver-specific subset of a ``users`` row."""
    user_id: int
    account_id: int
    display_name: str
    telegram_id: Optional[int]
    samsara_driver_id: Optional[str]
    # CDL
    cdl_number: Optional[str]
    cdl_state: Optional[str]
    cdl_class: Optional[str]
    cdl_expires: Optional[str]
    # Medical
    med_card_expires: Optional[str]
    # Employment
    hire_date: Optional[str]
    termination_date: Optional[str]
    # Contact
    dob: Optional[str]
    phone: Optional[str]
    home_address: Optional[str]
    driver_notes: Optional[str]
    # Primary vehicle assignment (denormalised cache from
    # ``driver_vehicle_assignments`` — see ``assign_vehicle_to_driver``).
    # Defaults to None so legacy callers that ignore the column still
    # construct cleanly.  The underlying SQL column is still
    # ``users.truck_num`` (kept for backwards compat with the
    # bot/alerts/routes code that reads ``User.truck_num`` directly);
    # this dataclass field uses the project-standard ``vehicle_name``
    # so new code reads naturally.
    vehicle_name: Optional[str] = None


@dataclass
class VehicleAssignment:
    """One row from driver_vehicle_assignments — preserves history."""
    id: int
    account_id: int
    user_id: int
    vehicle_name: str
    vehicle_id: Optional[str]
    is_primary: bool
    assigned_by: Optional[int]
    assigned_at: str
    unassigned_at: Optional[str]
    notes: Optional[str]


@dataclass
class DriverDocument:
    """One row from driver_documents — addresses an object in the ObjectStore."""
    id: int
    account_id: int
    user_id: int
    doc_type: str
    bucket: str
    object_key: str
    drive_file_id: Optional[str]
    file_name: str
    file_size: Optional[int]
    mime_type: Optional[str]
    issued_at: Optional[str]
    expires_at: Optional[str]
    status: str
    uploaded_by: Optional[int]
    uploaded_at: str
    notes: Optional[str]


# Allowed driver-profile columns the API may update.  Kept as an
# explicit set so an admin route can't poke at unrelated user fields
# like ``email`` or ``password_hash`` by accident.
_DRIVER_PROFILE_COLUMNS = frozenset({
    "cdl_number", "cdl_state", "cdl_class", "cdl_expires",
    "med_card_expires", "hire_date", "termination_date",
    "dob", "phone", "home_address", "driver_notes",
    "samsara_driver_id",
})

# Doc-type whitelist for the writer API.  Open-ended on read (legacy
# rows survive) but writes go through this set.
VALID_DOC_TYPES = frozenset({
    "cdl", "medical_card", "mvr", "drug_screen",
    "background_check", "training_cert", "other",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Integration roster reconciliation (Datatruck link) ─────────────────
#
# A driver IS a ``users`` row (role='driver'); integrations LINK to it and
# fill gaps — they never define it, and a sync tick never creates users
# (the one-time "Import from Datatruck" onboarding action does creation
# deliberately).  Match keys, strongest first:
#   1. an existing ``datatruck_driver_id`` (idempotent re-sync)
#   2. CDL / license number — encrypted at rest, so matched in memory on
#      the decrypted roster (fleets are small; never via SQL)
#   3. email — ``users.email`` is stored lowercased plaintext
# NAME IS NEVER A MATCH KEY (two "John Smith"s are common).
#
# Reconcilable fields are merged through the shared hub.  Existing values
# are operator-owned by default (``owner_fallback='manual'``): HR-entered
# data is never overwritten by a sync — integrations only fill gaps.
DRIVER_RECON_FIELDS = ("display_name", "phone")
DEFAULT_DRIVER_PRECEDENCE = {
    "display_name": ("datatruck", "samsara"),
    "phone":        ("datatruck", "samsara"),
}
_DRIVER_FIELD_LABELS = {"display_name": "Name", "phone": "Phone"}

# ``data_conflicts`` stores values in PLAINTEXT — never record a conflict
# for a PII-encrypted column (phone, cdl_number).  Those stay fill-only;
# only non-secret fields may surface in the review panel.
_DRIVER_CONFLICT_SAFE_FIELDS = frozenset({"display_name"})

# Upstream statuses that mark a Datatruck driver as no longer working —
# the onboarding import skips them (resurrecting ex-drivers pollutes HR);
# unknown/empty statuses import normally (permissive, and the plan shows
# every name before anything happens).
_INACTIVE_DRIVER_STATUSES = frozenset({
    "inactive", "terminated", "fired", "disabled", "archived",
    "deleted", "suspended",
})


def _parse_driver_provenance(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _norm_cdl(v: Any) -> str:
    """Normalize a license number for exact matching: uppercase, strip
    spaces/dashes (formatting varies between the TMS and HR entry)."""
    return str(v or "").strip().upper().replace(" ", "").replace("-", "")


def _extract_datatruck_cdl(r: dict) -> str:
    """Pull a license number out of a normalized Datatruck driver row.
    ``_norm_driver`` doesn't promote it, but the raw payload often carries
    it under one of several vendor spellings."""
    payload = r.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    payload = payload or {}
    for k in ("cdl_number", "cdl", "license_number", "licenseNumber",
              "license", "driver_license", "dl_number"):
        v = payload.get(k)
        if v:
            return str(v).strip()
    return ""


async def _apply_driver_field(
    db: Any, account_id: int, entity_id: int, field: str, value: Any,
) -> None:
    """Resolution applier for the 'driver' entity: write the chosen value
    onto the driver's ``users`` row (encrypting PII) and PIN it so no later
    sync undoes the operator's decision."""
    await db.write_driver_field_pinned(account_id, entity_id, field, value)


# Declare 'driver' with the shared reconciliation hub — precedence,
# conflict detection, and resolution now cover drivers with no new
# engine code (same one-call contract the vehicle registry uses).
recon.register_reconciled_entity(
    "driver",
    fields=DRIVER_RECON_FIELDS,
    default_precedence=DEFAULT_DRIVER_PRECEDENCE,
    field_labels=_DRIVER_FIELD_LABELS,
    sources=("datatruck", "samsara"),
    apply_resolution=_apply_driver_field,
)


# ── Profile mixin ──────────────────────────────────────────────


class DriverProfileMixin(_MixinBase):
    """Driver-profile read/write on the ``users`` table."""

    async def get_driver_profile(self, user_id: int) -> Optional[DriverProfile]:
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, display_name, telegram_id, "
                "       samsara_driver_id, cdl_number, cdl_state, cdl_class, "
                "       cdl_expires, med_card_expires, hire_date, "
                "       termination_date, dob, phone, home_address, "
                "       driver_notes "
                "FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return DriverProfile(
            user_id=row[0],
            account_id=row[1],
            display_name=row[2] or "",
            telegram_id=row[3],
            samsara_driver_id=row[4],
            # Encrypted-at-rest fields are decrypted on the way out so
            # every consumer (API, bot, exporter) sees plaintext without
            # caring about the storage representation.
            cdl_number=_maybe_decrypt(row[5]),
            cdl_state=row[6],
            cdl_class=row[7],
            cdl_expires=row[8],
            med_card_expires=row[9],
            hire_date=row[10],
            termination_date=row[11],
            dob=_maybe_decrypt(row[12]),
            phone=_maybe_decrypt(row[13]),
            home_address=_maybe_decrypt(row[14]),
            driver_notes=_maybe_decrypt(row[15]),
        )

    async def update_driver_profile(
        self, user_id: int, **fields,
    ) -> bool:
        """Update one or more profile fields.  Silently drops keys that
        aren't in the allow-list (defensive against API callers
        passing through arbitrary kwargs).

        PII columns are encrypted before they hit the DB.  When the
        ``ENCRYPTION_KEY`` env var is unset, ``encrypt`` is a no-op so
        local dev / test fixtures keep working with plaintext rows.
        """
        clean = {k: v for k, v in fields.items() if k in _DRIVER_PROFILE_COLUMNS}
        if not clean:
            return False
        # Encrypt PII before storage; other fields pass through.
        for col in list(clean.keys()):
            if col in _PII_COLUMNS:
                clean[col] = _maybe_encrypt(clean[col])
        # Operator edits PIN the reconcilable fields they touch (stamp
        # 'manual' in driver_field_provenance) so no integration sync
        # overwrites the human's correction — same Layer-2 semantic as
        # the vehicle registry.
        pin = [
            k for k in clean
            if k in DRIVER_RECON_FIELDS or k in ("cdl_number", "cdl_state", "cdl_class")
        ]
        if pin:
            try:
                cur = await self._db.execute(
                    "SELECT driver_field_provenance FROM users WHERE id = ?",
                    (user_id,),
                )
                row = await cur.fetchone()
                prov = _parse_driver_provenance(row[0]) if row else {}
                for f in pin:
                    prov[f] = recon.MANUAL_SOURCE
                clean["driver_field_provenance"] = json.dumps(prov)
            except Exception as e:
                # Pre-migration DB (column absent) — the update itself
                # must still work; pinning just doesn't apply yet.
                logger.debug("driver provenance pin skipped: %s", e)
        cols = ", ".join(f"{k} = ?" for k in clean)
        params = tuple(clean.values()) + (user_id,)
        async with self.transaction():
            cur = await self._db.execute(
                f"UPDATE users SET {cols} WHERE id = ?",
                params,
            )
        return cur.rowcount > 0

    async def write_driver_field_pinned(
        self, account_id: int, user_id: int, field: str, value: Any,
    ) -> None:
        """Write one reconcilable field onto a driver's ``users`` row and
        PIN it (provenance='manual').  Used by conflict resolution — the
        operator's chosen value must survive every later sync.  PII columns
        are encrypted on the way in; the write is account-scoped."""
        if field in _PII_COLUMNS:
            value = _maybe_encrypt(value)
        cur = await self._db.execute(
            "SELECT driver_field_provenance FROM users "
            "WHERE id = ? AND account_id = ?",
            (user_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return
        prov = _parse_driver_provenance(row[0])
        prov[field] = recon.MANUAL_SOURCE
        async with self.transaction():
            await self._db.execute(
                f"UPDATE users SET {field} = ?, driver_field_provenance = ? "
                "WHERE id = ? AND account_id = ?",
                (value, json.dumps(prov), user_id, account_id),
            )

    async def link_datatruck_driver(
        self, account_id: int, user_id: int, external_id: str,
    ) -> None:
        """Manually bind (or unbind, external_id='') a Datatruck driver to a
        member — the Team Management escape hatch for links the automatic
        CDL→email matcher correctly refused to guess.  A ref already bound
        to ANOTHER member raises (one person, one link)."""
        ref = (external_id or "").strip()
        if ref:
            cur = await self._db.execute(
                "SELECT id FROM users WHERE account_id = ? "
                "AND datatruck_driver_id = ? AND id <> ?",
                (account_id, ref, user_id),
            )
            if await cur.fetchone():
                raise ValueError(
                    "that Datatruck driver is already linked to another member",
                )
        async with self.transaction():
            await self._db.execute(
                "UPDATE users SET datatruck_driver_id = ? "
                "WHERE id = ? AND account_id = ?",
                (ref or None, user_id, account_id),
            )

    async def get_datatruck_driver_name(
        self, account_id: int, external_id: str,
    ) -> str:
        """Display name of a staged Datatruck driver (for loads backfill)."""
        cur = await self._db.execute(
            "SELECT display_name FROM datatruck_drivers "
            "WHERE account_id = ? AND external_id = ?",
            (account_id, external_id),
        )
        row = await cur.fetchone()
        return str(row[0] or "") if row else ""

    async def _driver_match_state(self, account_id: int):
        """The in-memory match indexes shared by the ongoing projection and
        the one-time import: the decrypted roster plus lookups by existing
        Datatruck ref / normalized CDL / lowercased email (keys shared by
        more than one driver are ambiguous → tracked in the dupe sets).
        Returns None when the account has no drivers at all."""
        profiles = await self.list_drivers(account_id)  # decrypted CDL/phone
        if not profiles:
            return None
        # email / link / provenance aren't on DriverProfile — one extra read.
        cur = await self._db.execute(
            "SELECT id, email, datatruck_driver_id, driver_field_provenance "
            "FROM users WHERE account_id = ? AND role = 'driver'",
            (account_id,),
        )
        extra = {
            r[0]: (str(r[1] or ""), str(r[2] or ""), _parse_driver_provenance(r[3]))
            for r in await cur.fetchall()
        }
        by_ref: dict[str, Any] = {}
        by_cdl: dict[str, Any] = {}
        by_email: dict[str, Any] = {}
        cdl_dupes: set[str] = set()
        email_dupes: set[str] = set()
        for p in profiles:
            email, ref, _prov = extra.get(p.user_id, ("", "", {}))
            if ref:
                by_ref[ref] = p
            cdl = _norm_cdl(p.cdl_number)
            if cdl:
                if cdl in by_cdl:
                    cdl_dupes.add(cdl)
                else:
                    by_cdl[cdl] = p
            em = email.strip().lower()
            if em:
                if em in by_email:
                    email_dupes.add(em)
                else:
                    by_email[em] = p
        return profiles, extra, by_ref, by_cdl, by_email, cdl_dupes, email_dupes

    async def plan_datatruck_driver_import(self, account_id: int) -> dict:
        """Read-only plan for the one-time "Import drivers from Datatruck"
        onboarding action, over the synced ``datatruck_drivers`` staging.

        Classifies every staged driver:
          * ``already`` — linked on a previous pass (has the ref) → no action.
          * ``link``    — matches an existing driver-user by CDL/email →
            apply would LINK it (no creation).
          * ``create``  — no match → apply would create a 4truck user with
            the Driver role (roster entry: no login until invited).
          * ``review``  — the match key is shared by >1 existing driver →
            ambiguous, never guessed; a human resolves it.
          * ``skipped`` — inactive/terminated upstream → not imported.
        """
        staged = await self.list_datatruck_drivers(account_id)
        state = await self._driver_match_state(account_id)
        if state is None:
            by_ref, by_cdl, by_email = {}, {}, {}
            cdl_dupes, email_dupes = set(), set()
        else:
            _p, _e, by_ref, by_cdl, by_email, cdl_dupes, email_dupes = state

        already = 0
        link: list[dict] = []
        create: list[dict] = []
        review: list[dict] = []
        skipped: list[dict] = []
        for d in staged:
            ref = (d.external_id or "").strip()
            if not ref:
                continue
            entry = {
                "external_id": ref,
                "name": d.display_name or f"{d.first_name} {d.last_name}".strip(),
                "phone": d.phone, "email": d.email, "status": d.status,
            }
            if str(d.status or "").strip().lower() in _INACTIVE_DRIVER_STATUSES:
                skipped.append({**entry, "reason": "inactive in Datatruck"})
                continue
            if ref in by_ref:
                already += 1
                continue
            cdl = _norm_cdl(_extract_datatruck_cdl(
                {"payload": d.payload},
            ))
            em = (d.email or "").strip().lower()
            if (cdl and cdl in cdl_dupes) or (em and em in email_dupes):
                review.append({
                    **entry,
                    "reason": "match key shared by multiple existing drivers",
                })
                continue
            match = (by_cdl.get(cdl) if cdl else None) or \
                    (by_email.get(em) if em else None)
            if match is not None:
                link.append({
                    **entry,
                    "matched_user_id": match.user_id,
                    "matched_name": match.display_name,
                    "by": "cdl" if (cdl and by_cdl.get(cdl) is match) else "email",
                })
            else:
                create.append(entry)
        return {
            "kind": "drivers",
            "already_linked": already,
            "link": link, "create": create,
            "review": review, "skipped": skipped,
            "counts": {
                "already_linked": already, "link": len(link),
                "create": len(create), "review": len(review),
                "skipped": len(skipped),
            },
        }

    async def apply_datatruck_driver_import(self, account_id: int) -> dict:
        """Apply the import plan: CREATE a driver-user for every unmatched
        active Datatruck driver (telegram NULL, no password — a roster
        entry until invited; phone/CDL encrypted, provenance=datatruck,
        ``datatruck_driver_id`` stamped), then run the ongoing projection
        once so the link-bucket gets linked/enriched.  Idempotent — a
        second apply finds everyone already linked and creates nothing."""
        plan = await self.plan_datatruck_driver_import(account_id)
        now = self._now()
        created = 0
        for c in plan["create"]:
            prov = {"display_name": "datatruck"}
            phone_enc = None
            if c.get("phone"):
                phone_enc = _maybe_encrypt(c["phone"])
                prov["phone"] = "datatruck"
            await self._db.execute(
                """INSERT INTO users
                   (telegram_id, account_id, role, display_name, email,
                    phone, datatruck_driver_id, driver_field_provenance,
                    created_at)
                   VALUES (?, ?, 'driver', ?, ?, ?, ?, ?, ?)""",
                (
                    None, account_id,
                    c["name"] or f"Driver {c['external_id']}",
                    (c.get("email") or "").strip().lower() or None,
                    phone_enc, c["external_id"], json.dumps(prov), now,
                ),
            )
            created += 1
        await self._db.commit()
        # One projection pass links the link-bucket + enriches the newly
        # created rows (fills CDL from the payload via the normal path).
        staged = await self.list_datatruck_drivers(account_id)
        rows = [
            {
                "external_id": d.external_id,
                "display_name": d.display_name,
                "phone": d.phone, "email": d.email, "status": d.status,
                "payload": d.payload,
            }
            for d in staged
            if str(d.status or "").strip().lower() not in _INACTIVE_DRIVER_STATUSES
        ]
        linked = await self.project_datatruck_drivers(account_id, rows)
        return {
            "created": created,
            "linked": linked,
            "review": plan["counts"]["review"],
            "skipped": plan["counts"]["skipped"],
        }

    async def project_datatruck_drivers(
        self, account_id: int, rows: list[dict],
    ) -> int:
        """Link a synced Datatruck driver list onto the account's EXISTING
        driver-users — enrich, never create (the one-time "Import from
        Datatruck" onboarding action creates; a sync tick must not).

        Match priority per incoming row:
          1. already-linked ``datatruck_driver_id`` (idempotent re-sync)
          2. CDL / license number — decrypted in memory, normalized exact
          3. email — ``users.email`` lowercased exact
        A key shared by >1 driver is ambiguous → skipped (never guess).

        Matched drivers merge display_name/phone through the reconciliation
        hub (existing values are operator-owned → fill-empty only), fill an
        empty ``cdl_number`` from the payload (encrypted, no conflict
        record — conflicts store plaintext and CDL/phone are PII), and get
        ``datatruck_driver_id`` stamped.  Returns rows linked-or-updated.
        """
        if not rows:
            return 0
        state = await self._driver_match_state(account_id)
        if state is None:
            return 0
        profiles, extra, by_ref, by_cdl, by_email, cdl_dupes, email_dupes = state

        precedence = await recon.get_precedence(self, account_id, "driver")
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                ref = str(r.get("external_id") or "").strip()
                if not ref:
                    continue
                inc_cdl = _extract_datatruck_cdl(r)
                p = by_ref.get(ref)
                if p is None and inc_cdl:
                    key = _norm_cdl(inc_cdl)
                    if key and key not in cdl_dupes:
                        p = by_cdl.get(key)
                if p is None:
                    em = str(r.get("email") or "").strip().lower()
                    if em and em not in email_dupes:
                        p = by_email.get(em)
                if p is None:
                    continue  # left for the onboarding import to create

                _email, ref_existing, prov = extra.get(p.user_id, ("", "", {}))
                mr = recon.merge_fields(
                    current={"display_name": p.display_name, "phone": p.phone},
                    provenance=prov,
                    owner_fallback=recon.MANUAL_SOURCE,
                    incoming=r, source="datatruck",
                    fields=DRIVER_RECON_FIELDS, precedence=precedence,
                )
                sets: list[str] = []
                params: list[Any] = []
                for f in DRIVER_RECON_FIELDS:
                    if f in mr.updates:
                        sets.append(f"{f} = ?")
                        params.append(
                            _maybe_encrypt(mr.updates[f])
                            if f in _PII_COLUMNS else mr.updates[f],
                        )
                # Fill an EMPTY CDL from the payload (fill-only, encrypted).
                if inc_cdl and not (p.cdl_number or "").strip():
                    sets.append("cdl_number = ?")
                    params.append(_maybe_encrypt(inc_cdl))
                    mr.provenance["cdl_number"] = "datatruck"
                if ref_existing != ref:
                    sets.append("datatruck_driver_id = ?")
                    params.append(ref)
                if not sets and not mr.cleared:
                    continue  # nothing to change for this driver
                sets.append("driver_field_provenance = ?")
                params.append(json.dumps(mr.provenance))
                params.extend([p.user_id, account_id])
                await self._db.execute(
                    f"UPDATE users SET {', '.join(sets)} "
                    "WHERE id = ? AND account_id = ?",
                    tuple(params),
                )
                written += 1
                safe_conflicts = [
                    c for c in mr.conflicts
                    if c["field"] in _DRIVER_CONFLICT_SAFE_FIELDS
                ]
                safe_cleared = [
                    f for f in mr.cleared if f in _DRIVER_CONFLICT_SAFE_FIELDS
                ]
                if safe_conflicts or safe_cleared:
                    conflict_ops.append((p.user_id, safe_conflicts, safe_cleared))
        await recon.sync_batch(self, account_id, "driver", conflict_ops)
        return written

    async def list_drivers(
        self, account_id: int, include_terminated: bool = False,
    ) -> list[DriverProfile]:
        """All drivers in an account.  Excludes terminated by default."""
        where = "WHERE account_id = ? AND role = 'driver' AND is_active = 1"
        if not include_terminated:
            where += " AND (termination_date IS NULL OR termination_date = '')"
        async with self.acquire() as conn:
            cur = await conn.execute(
                f"SELECT id, account_id, display_name, telegram_id, "
                f"       samsara_driver_id, cdl_number, cdl_state, cdl_class, "
                f"       cdl_expires, med_card_expires, hire_date, "
                f"       termination_date, dob, phone, home_address, "
                f"       driver_notes, truck_num AS vehicle_name "
                f"FROM users {where} "
                f"ORDER BY LOWER(display_name)",
                (account_id,),
            )
            rows = await cur.fetchall()
        return [
            DriverProfile(
                user_id=r[0], account_id=r[1], display_name=r[2] or "",
                telegram_id=r[3], samsara_driver_id=r[4],
                # PII fields decrypted on the way out.  See ``_PII_COLUMNS``.
                cdl_number=_maybe_decrypt(r[5]),
                cdl_state=r[6], cdl_class=r[7], cdl_expires=r[8],
                med_card_expires=r[9], hire_date=r[10], termination_date=r[11],
                dob=_maybe_decrypt(r[12]),
                phone=_maybe_decrypt(r[13]),
                home_address=_maybe_decrypt(r[14]),
                driver_notes=_maybe_decrypt(r[15]),
                vehicle_name=r[16],
            )
            for r in rows
        ]


# ── Vehicle-assignment mixin (history-preserving) ──────────────


class DriverVehicleAssignmentsMixin(_MixinBase):
    """Single source of truth for driver↔vehicle mapping.

    Replaces the legacy ``driver_trucks`` table.  All writes go
    through here; reads in alerts/scorecards/etc. fall back to
    ``driver_trucks`` and ``users.truck_num`` during the transition
    period (handled by ``get_user_vehicle_nums``).
    """

    async def assign_vehicle_to_driver(
        self,
        account_id: int,
        user_id: int,
        vehicle_name: str,
        vehicle_id: Optional[str] = None,
        is_primary: bool = False,
        assigned_by: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> VehicleAssignment:
        """Create an active assignment.  When ``is_primary=True`` the
        existing primary (if any) is demoted to secondary in the
        same transaction so there's always at most one primary."""
        now = _now_iso()
        vehicle_name = vehicle_name.strip()
        async with self.transaction():
            if is_primary:
                await self._db.execute(
                    "UPDATE driver_vehicle_assignments "
                    "SET is_primary = 0 "
                    "WHERE user_id = ? AND unassigned_at IS NULL",
                    (user_id,),
                )
            cur = await self._db.execute(
                "INSERT INTO driver_vehicle_assignments "
                "(account_id, user_id, vehicle_name, vehicle_id, "
                " is_primary, assigned_by, assigned_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, user_id, vehicle_name, vehicle_id,
                 int(is_primary), assigned_by, now, notes),
            )
            new_id = cur.lastrowid
            # Sync the denormalized cache on users.truck_num so legacy
            # readers (alert pipeline driver-filter, AI agent) stay
            # consistent with the new source of truth.  Only on primary.
            if is_primary:
                await self._db.execute(
                    "UPDATE users SET truck_num = ? WHERE id = ?",
                    (vehicle_name, user_id),
                )
        logger.info(
            "Vehicle assigned to driver: user=%d vehicle=%s primary=%s",
            user_id, vehicle_name, is_primary,
        )
        return VehicleAssignment(
            id=new_id, account_id=account_id, user_id=user_id,
            vehicle_name=vehicle_name, vehicle_id=vehicle_id,
            is_primary=is_primary, assigned_by=assigned_by,
            assigned_at=now, unassigned_at=None, notes=notes,
        )

    async def end_vehicle_assignment(
        self, assignment_id: int, *, by: Optional[int] = None,
    ) -> bool:
        """Mark an active assignment as unassigned.  Preserves the
        row for history.  Returns True if a row was updated.

        Named ``end_vehicle_assignment`` rather than
        ``unassign_vehicle`` to avoid colliding with the legacy
        ``DriverVehiclesMixin.unassign_vehicle(user_id, truck_num)``
        method, which has different semantics (deletes the
        ``driver_trucks`` row outright rather than preserving
        history).  The new method works on the new
        ``driver_vehicle_assignments`` table only."""
        now = _now_iso()
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_vehicle_assignments "
                "SET unassigned_at = ? "
                "WHERE id = ? AND unassigned_at IS NULL",
                (now, assignment_id),
            )
            updated = cur.rowcount > 0
            if updated:
                # If the unassigned row was the primary, clear the
                # denormalized cache on users.truck_num.  A new
                # primary (if any) overwrites it on the next assign.
                await self._db.execute(
                    "UPDATE users SET truck_num = NULL "
                    "WHERE id IN ("
                    "    SELECT user_id FROM driver_vehicle_assignments "
                    "    WHERE id = ? AND is_primary = 1"
                    ")",
                    (assignment_id,),
                )
        if updated and by is not None:
            logger.info("Vehicle unassignment: assignment_id=%d by=%d", assignment_id, by)
        return updated

    async def list_active_assignments(self, user_id: int) -> list[VehicleAssignment]:
        """All currently-active assignments for a driver, primary first."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, vehicle_name, vehicle_id, "
                "       is_primary, assigned_by, assigned_at, "
                "       unassigned_at, notes "
                "FROM driver_vehicle_assignments "
                "WHERE user_id = ? AND unassigned_at IS NULL "
                "ORDER BY is_primary DESC, vehicle_name",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_assignment(r) for r in rows]

    async def list_assignment_history(self, user_id: int) -> list[VehicleAssignment]:
        """Active + historical assignments for a driver (newest first)."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, vehicle_name, vehicle_id, "
                "       is_primary, assigned_by, assigned_at, "
                "       unassigned_at, notes "
                "FROM driver_vehicle_assignments "
                "WHERE user_id = ? "
                "ORDER BY assigned_at DESC",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_assignment(r) for r in rows]

    @staticmethod
    def _row_to_assignment(r) -> VehicleAssignment:
        return VehicleAssignment(
            id=r[0], account_id=r[1], user_id=r[2],
            vehicle_name=r[3], vehicle_id=r[4],
            is_primary=bool(r[5]), assigned_by=r[6],
            assigned_at=r[7], unassigned_at=r[8], notes=r[9],
        )


# ── Document store + storage quota ─────────────────────────────


class _StorageQuotaExceeded(Exception):
    """Raised when an upload would push an account over its local-disk
    quota.  API layer translates this to HTTP 429 with a friendly
    message + current usage/quota numbers."""


class DriverDocumentsMixin(_MixinBase):
    """Per-driver document store with expiration tracking."""

    StorageQuotaExceeded = _StorageQuotaExceeded

    async def add_document(
        self,
        *,
        account_id: int,
        user_id: int,
        doc_type: str,
        bucket: str,
        object_key: str,
        file_name: str,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        drive_file_id: Optional[str] = None,
        issued_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> DriverDocument:
        if doc_type not in VALID_DOC_TYPES:
            raise ValueError(f"Unknown doc_type: {doc_type}")
        now = _now_iso()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT INTO driver_documents "
                "(account_id, user_id, doc_type, bucket, object_key, "
                " drive_file_id, file_name, file_size, mime_type, "
                " issued_at, expires_at, status, uploaded_by, "
                " uploaded_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        'active', ?, ?, ?)",
                (account_id, user_id, doc_type, bucket, object_key,
                 drive_file_id, file_name, file_size, mime_type,
                 issued_at, expires_at, uploaded_by, now, notes),
            )
            new_id = cur.lastrowid
            # Bump per-account storage usage (only meaningful for the
            # local-disk fallback; Drive accounts cap on their side).
            if file_size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = storage_used_bytes + ? "
                    "WHERE id = ?",
                    (file_size, account_id),
                )
        logger.info(
            "Driver doc uploaded: user=%d type=%s key=%s size=%s",
            user_id, doc_type, object_key, file_size,
        )
        return DriverDocument(
            id=new_id, account_id=account_id, user_id=user_id,
            doc_type=doc_type, bucket=bucket, object_key=object_key,
            drive_file_id=drive_file_id, file_name=file_name,
            file_size=file_size, mime_type=mime_type,
            issued_at=issued_at, expires_at=expires_at,
            status="active", uploaded_by=uploaded_by,
            uploaded_at=now, notes=notes,
        )

    async def list_documents(
        self,
        user_id: int,
        *,
        status: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[DriverDocument]:
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if doc_type is not None:
            clauses.append("doc_type = ?")
            params.append(doc_type)
        where = " AND ".join(clauses)
        async with self.acquire() as conn:
            cur = await conn.execute(
                f"SELECT id, account_id, user_id, doc_type, bucket, "
                f"       object_key, drive_file_id, file_name, file_size, "
                f"       mime_type, issued_at, expires_at, status, "
                f"       uploaded_by, uploaded_at, notes "
                f"FROM driver_documents WHERE {where} "
                f"ORDER BY uploaded_at DESC",
                tuple(params),
            )
            rows = await cur.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def get_document(self, doc_id: int) -> Optional[DriverDocument]:
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, doc_type, bucket, "
                "       object_key, drive_file_id, file_name, file_size, "
                "       mime_type, issued_at, expires_at, status, "
                "       uploaded_by, uploaded_at, notes "
                "FROM driver_documents WHERE id = ?",
                (doc_id,),
            )
            row = await cur.fetchone()
        return self._row_to_document(row) if row else None

    async def delete_document(self, doc_id: int) -> bool:
        """Remove the DB row + decrement the account's storage usage.
        Caller is responsible for deleting the underlying object from
        the ``ObjectStore`` (so a failure to delete the file doesn't
        orphan the DB row, or vice-versa)."""
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT account_id, file_size FROM driver_documents "
                "WHERE id = ?",
                (doc_id,),
            )
            row = await cur.fetchone()
            if not row:
                return False
            acct_id, size = row
            await self._db.execute(
                "DELETE FROM driver_documents WHERE id = ?",
                (doc_id,),
            )
            if size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = MAX(0, storage_used_bytes - ?) "
                    "WHERE id = ?",
                    (size, acct_id),
                )
        return True

    async def move_user_documents_bucket(
        self, user_id: int, old_bucket: str, new_bucket: str,
    ) -> int:
        """Rewrite the ``bucket`` column on every driver_documents row
        belonging to ``user_id`` that currently points to ``old_bucket``.

        Used by the driver-company-change archive flow:  the
        ObjectStore moves the physical folder (one Drive re-parent
        call or a ``shutil.move``), and this method updates the DB
        rows so subsequent ``get_document`` reads resolve to the new
        path.  Returns the number of rows updated.
        """
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_documents "
                "SET bucket = ? "
                "WHERE user_id = ? AND bucket = ?",
                (new_bucket, user_id, old_bucket),
            )
        return cur.rowcount or 0

    async def get_expiring_documents(
        self,
        account_id: int,
        *,
        within_days: int = 30,
    ) -> list[DriverDocument]:
        """Active documents expiring within ``within_days`` from today.

        Powers the daily expiration-alert scheduler.  Compares
        ``expires_at`` (ISO date string) to today's date — past-due
        docs are also returned so the scheduler can flip them to
        ``status='expired'``.
        """
        # Cutoff is computed in Python so the SQL works on both SQLite
        # (no ``date()`` quirks) and PostgreSQL (no ``date(?, ?)`` 2-arg
        # form).  ISO date strings sort lexicographically the same as
        # the underlying dates, so we can compare ``expires_at`` directly.
        from datetime import timedelta as _td
        today = datetime.now(timezone.utc).date()
        cutoff = (today + _td(days=within_days)).isoformat()
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, doc_type, bucket, "
                "       object_key, drive_file_id, file_name, file_size, "
                "       mime_type, issued_at, expires_at, status, "
                "       uploaded_by, uploaded_at, notes "
                "FROM driver_documents "
                "WHERE account_id = ? AND status = 'active' "
                "  AND expires_at IS NOT NULL "
                "  AND expires_at <= ? "
                "ORDER BY expires_at ASC",
                (account_id, cutoff),
            )
            rows = await cur.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def mark_document_status(self, doc_id: int, status: str) -> bool:
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_documents SET status = ? WHERE id = ?",
                (status, doc_id),
            )
        return cur.rowcount > 0

    # ── Expiration-alert dedup ledger ──────────────────────────

    async def record_doc_notification(
        self, doc_id: int, bucket_days: int,
    ) -> bool:
        """Insert a ledger row marking that we've sent the T-``bucket_days``
        alert for this document.  Returns True if newly inserted
        (i.e. the caller should fire the alert), False if a row for
        this ``(doc_id, bucket_days)`` already exists (dedup hit).

        The composite PK enforces uniqueness at the DB level, so this
        is safe to call from concurrent schedulers.
        """
        # ``INSERT OR IGNORE`` is auto-translated to PostgreSQL's
        # ``ON CONFLICT DO NOTHING`` by the pg_adapter — that's the
        # portable way to express "insert if absent, do nothing if
        # already there".  We then read ``cur.rowcount`` to tell fresh
        # (1) from dedup (0).  Catching an exception around a raw
        # INSERT wouldn't work on PG because asyncpg aborts the whole
        # transaction on constraint violation.
        now = datetime.now(timezone.utc).isoformat()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO driver_document_notifications "
                "(doc_id, bucket_days, notified_at) VALUES (?, ?, ?)",
                (doc_id, bucket_days, now),
            )
        return cur.rowcount > 0

    async def get_doc_notification_buckets(self, doc_id: int) -> list[int]:
        """Return which bucket-day thresholds have already fired for
        this document.  Used by tests and admin diagnostics."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT bucket_days FROM driver_document_notifications "
                "WHERE doc_id = ? ORDER BY bucket_days DESC",
                (doc_id,),
            )
            rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    @staticmethod
    def _row_to_document(r) -> DriverDocument:
        return DriverDocument(
            id=r[0], account_id=r[1], user_id=r[2], doc_type=r[3],
            bucket=r[4], object_key=r[5], drive_file_id=r[6],
            file_name=r[7], file_size=r[8], mime_type=r[9],
            issued_at=r[10], expires_at=r[11], status=r[12],
            uploaded_by=r[13], uploaded_at=r[14], notes=r[15],
        )

    # ── Storage quota (local-disk fallback) ────────────────────

    async def get_storage_usage(self, account_id: int) -> tuple[int, int]:
        """Return ``(used_bytes, quota_bytes)`` for the account.
        ``quota_bytes`` defaults to 500 MB on fresh accounts (the
        migration default)."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(storage_used_bytes, 0), "
                "       COALESCE(storage_quota_bytes, 524288000) "
                "FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await cur.fetchone()
        if not row:
            return (0, 524288000)
        return (int(row[0] or 0), int(row[1] or 524288000))

    async def enforce_storage_quota(
        self, account_id: int, additional_bytes: int,
    ) -> None:
        """Raise ``StorageQuotaExceeded`` if adding ``additional_bytes``
        would push the account over its cap.

        Only meaningful when the account uses the local-disk
        fallback ObjectStore; the API layer should skip this check
        when the account is connected to Google Drive (which has
        its own quota on the user's Drive)."""
        used, quota = await self.get_storage_usage(account_id)
        if used + additional_bytes > quota:
            raise _StorageQuotaExceeded(
                f"account {account_id}: upload of {additional_bytes} bytes "
                f"would exceed quota ({used} / {quota} used)"
            )

    async def set_storage_quota(self, account_id: int, quota_bytes: int) -> bool:
        """Admin route uses this to raise/lower the per-account cap."""
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE accounts SET storage_quota_bytes = ? WHERE id = ?",
                (quota_bytes, account_id),
            )
        return cur.rowcount > 0
