"""Driver-recruiting mixin — recruitment links + driver applications.

Recruiters create shareable links (``application_links``); prospective
drivers submit applications through them (``driver_applications``).  PII
is encrypted at rest via ``infra.crypto``: the scalar DOB/SSN columns plus
the JSON blobs that carry sensitive identifiers (full address + history,
CDL/licence number, employment history, accident/violation record) — see
``_ENCRYPTED_JSON_COLS``.  Uploaded docs live in the object store (only
their ids are stored here).
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object

# JSON columns encrypted at rest because they carry PII beyond the
# cleartext promoted columns (first/last/email/phone/city/state, kept
# plaintext for the recruiter list + search).  Encrypted symmetrically:
# ``encrypt(json.dumps(...))`` on write, ``decrypt(...)`` before
# ``json.loads`` on read.  ``infra.crypto.decrypt`` passes legacy
# plaintext through unchanged, so pre-existing rows still read fine and
# no backfill migration is needed.
_ENCRYPTED_JSON_COLS = frozenset({
    "personal_json", "address_history_json", "cdl_json",
    "employment_json", "incidents_json",
})

# Sentinel for partial-update args that may legitimately be set to NULL
# (so "not provided" is distinct from "clear to NULL").
_UNSET: Any = object()


class ApplicationsMixin(_MixinBase):

    # ── Recruitment links ───────────────────────────────────────────

    async def create_application_link(
        self, account_id: int, *, label: str = "", source: str = "",
        created_by: int | None = None, expires_in_days: int | None = None,
        company_id: int | None = None,
    ) -> dict:
        """Mint a shareable recruiting link.  Returns the row incl. token.

        ``expires_in_days`` sets an auto-close window (None → never expires,
        for backward compatibility); the public form rejects the token once
        past ``expires_at``.
        """
        from datetime import datetime, timezone, timedelta
        token = secrets.token_urlsafe(18)
        now = self._now()
        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=expires_in_days)
            ).isoformat()
        cur = await self._db.execute(
            """INSERT INTO application_links
                 (account_id, token, label, source, created_by, is_active,
                  created_at, expires_at, company_id)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (account_id, token, label, source, created_by, now, expires_at, company_id),
        )
        await self._db.commit()
        return {
            "id": cur.lastrowid, "account_id": account_id, "token": token,
            "label": label, "source": source, "is_active": True,
            "created_at": now, "expires_at": expires_at, "company_id": company_id,
        }

    async def list_application_links(self, account_id: int) -> list[dict]:
        """Links with per-link funnel stats: views (column) + submissions
        and hires (derived from driver_applications on the token)."""
        cur = await self._db.execute(
            """
            SELECT l.*,
                   co.code AS company_code, co.display_name AS company_name,
                   (co.logo_object_id IS NOT NULL) AS company_has_logo,
                   COUNT(a.id) AS submissions,
                   COALESCE(SUM(CASE WHEN a.status = 'hired' THEN 1 ELSE 0 END), 0) AS hires
              FROM application_links l
              LEFT JOIN companies co ON co.id = l.company_id
              LEFT JOIN driver_applications a
                     ON a.link_token = l.token AND a.account_id = l.account_id
             WHERE l.account_id = ?
             GROUP BY l.id, co.id
             ORDER BY l.id DESC
            """,
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def increment_link_view(self, token: str) -> None:
        """Bump a link's view counter — best-effort, oracle-safe (the
        caller always returns 204 regardless of whether the token matched,
        so this never reveals which tokens exist)."""
        await self._db.execute(
            "UPDATE application_links SET view_count = view_count + 1 "
            "WHERE token = ? AND is_active = 1",
            (token,),
        )
        await self._db.commit()

    async def get_application_link_by_id(self, link_id: int) -> Optional[dict]:
        """Unscoped fetch — ONLY for the platform-wide nightly sweep, which
        has no user and no account context. Every request-path read must go
        through the account-scoped list/update helpers."""
        cur = await self._db.execute(
            "SELECT * FROM application_links WHERE id = ?", (link_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def resolve_application_link(self, token: str) -> Optional[dict]:
        """Map a public token → its account.  None if unknown/inactive.

        The public applicant form calls this with the /apply/<token>
        segment; a None result must surface as a uniform 404 (no oracle
        for which tokens exist).
        """
        cur = await self._db.execute(
            "SELECT * FROM application_links WHERE token = ? AND is_active = 1 "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (token, self._now()),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_application_link_active(
        self, account_id: int, link_id: int, active: bool,
    ) -> bool:
        cur = await self._db.execute(
            "UPDATE application_links SET is_active = ? "
            "WHERE id = ? AND account_id = ?",
            (1 if active else 0, link_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_application_link(
        self, account_id: int, link_id: int, *,
        label: Optional[str] = None, source: Optional[str] = None,
        company_id: Any = _UNSET, expires_at: Any = _UNSET,
        remind_every_hours: Optional[int] = None, remind_max: Optional[int] = None,
    ) -> bool:
        """Edit an existing link's label/source/carrier/expiry/auto-remind.
        Only the fields supplied are written (``company_id``/``expires_at``
        use a sentinel so they can be explicitly set to NULL).  Account-scoped."""
        sets: list[str] = []
        vals: list[Any] = []
        if label is not None:
            sets.append("label = ?")
            vals.append(label)
        if source is not None:
            sets.append("source = ?")
            vals.append(source)
        if company_id is not _UNSET:
            sets.append("company_id = ?")
            vals.append(company_id)
        if expires_at is not _UNSET:
            sets.append("expires_at = ?")
            vals.append(expires_at)
        if remind_every_hours is not None:
            sets.append("remind_every_hours = ?")
            vals.append(int(remind_every_hours))
        if remind_max is not None:
            sets.append("remind_max = ?")
            vals.append(int(remind_max))
        if not sets:
            return False
        vals += [link_id, account_id]
        cur = await self._db.execute(
            f"UPDATE application_links SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ?",
            tuple(vals),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_application_link(self, account_id: int, link_id: int) -> bool:
        """Permanently remove a link row (account-scoped).  Submitted
        applications are keyed by ``link_token`` text, NOT a FK, so they are
        left intact — only the shareable link + its view counter go away."""
        cur = await self._db.execute(
            "DELETE FROM application_links WHERE id = ? AND account_id = ?",
            (link_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Driver applications ─────────────────────────────────────────

    async def create_driver_application(
        self, account_id: int, *, link_token: str, reference: str,
        data: dict, docs: dict, submit_ip: str = "", company_id: int | None = None,
    ) -> dict:
        """Persist a submitted application.

        ``data`` is the validated, normalised 8-step payload (nested by
        section).  ``docs`` maps each document slot → its object-store id
        (the endpoint writes the files before calling this).  DOB/SSN are
        encrypted; everything else lands in promoted columns + JSON.
        """
        from infra import crypto
        from infra.crypto import encrypt

        # Visibility: keyless mode silently stores FMCSA PII (SSN, DOB,
        # licence, address) as plaintext.  The system supports keyless dev
        # by design, but a misconfigured PROD must not fail this guarantee
        # silently — warn loudly on every PII write so it's never invisible.
        if not crypto.is_enabled():
            logger.warning(
                "ENCRYPTION_KEY not configured — driver-application PII "
                "(SSN/DOB/licence/address) is being stored in PLAINTEXT "
                "for account_id=%s. Set ENCRYPTION_KEY to encrypt at rest.",
                account_id,
            )

        # Encrypt sensitive JSON blobs (see _ENCRYPTED_JSON_COLS).
        def _ej(value) -> str:
            return encrypt(json.dumps(value))

        personal = data.get("personal") or {}
        cdl = data.get("cdl") or {}
        position = data.get("position") or {}
        experience = data.get("experience") or {}
        consents = data.get("consents") or {}
        now = self._now()

        dob_enc = encrypt(personal["dob"]) if personal.get("dob") else None
        ssn_enc = encrypt(personal["ssn"]) if personal.get("ssn") else None
        # Keyed, per-account blind index of the SSN for recruiter-side
        # re-applicant detection — one-way, never exposed (see blind_index).
        from infra.crypto import blind_index
        ssn_hash = blind_index(personal["ssn"], scope=str(account_id)) if personal.get("ssn") else None

        cur = await self._db.execute(
            """
            INSERT INTO driver_applications (
                account_id, link_token, reference, status,
                first_name, last_name, email, phone, city, state,
                cdl_state, cdl_class, position_type, years_cdl,
                dob_enc, ssn_enc,
                gate_json, personal_json, address_history_json, cdl_json,
                experience_json, employment_json, incidents_json,
                position_json, consents_json, docs_json,
                sig_mode, sig_name, sig_date, sig_object_id,
                disclosure_version, ssn_hash, submit_ip, company_id,
                submitted_at, created_at
            ) VALUES (
                ?, ?, ?, 'submitted',
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                account_id, link_token, reference,
                personal.get("first", ""), personal.get("last", ""),
                personal.get("email", ""), personal.get("phone", ""),
                personal.get("city", ""), personal.get("state", ""),
                cdl.get("state", ""), cdl.get("class", ""),
                position.get("type", ""), experience.get("yearsCdl", ""),
                dob_enc, ssn_enc,
                json.dumps(data.get("gate") or {}),
                _ej({k: v for k, v in personal.items() if k not in ("dob", "ssn")}),
                _ej(data.get("addressHistory") or []),
                _ej({k: v for k, v in cdl.items() if k != "docs"}),
                json.dumps(experience),
                _ej(data.get("employment") or []),
                _ej(data.get("incidents") or {}),
                json.dumps(position),
                json.dumps({k: v for k, v in consents.items()
                            if k not in ("sigDataUrl",)}),
                json.dumps(docs or {}),
                consents.get("sigMode", ""), consents.get("sigName", ""),
                consents.get("sigDate", ""), docs.get("signature"),
                data.get("disclosureVersion", ""), ssn_hash, submit_ip, company_id,
                now, now,
            ),
        )
        await self._db.commit()
        return {"id": cur.lastrowid, "reference": reference, "status": "submitted"}

    # ── Nightly link-expiry sweep ───────────────────────────────────
    #
    # Cross-account by design: one scheduler pass for the platform, fanned
    # out per row. One-shot per link — the marker is stamped as the mail
    # goes out, so a retry can never double-send.

    async def list_links_expiring_soon(
        self, *, now_iso: str, before_iso: str,
    ) -> list[dict]:
        """Live links lapsing inside the warning window that nobody has
        been warned about yet."""
        cur = await self._db.execute(
            "SELECT id, account_id, label, source, expires_at, company_id "
            "  FROM application_links "
            " WHERE is_active = 1 "
            "   AND expires_at IS NOT NULL "
            "   AND expires_at > ? AND expires_at <= ? "
            "   AND expiry_warned_at = '' "
            " ORDER BY expires_at",
            (now_iso, before_iso),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_link_expiry_warned(self, link_id: int) -> None:
        await self._db.execute(
            "UPDATE application_links SET expiry_warned_at = ? WHERE id = ?",
            (self._now(), link_id),
        )
        await self._db.commit()

    async def count_driver_applications(
        self, account_id: int, *, status: str = "",
    ) -> int:
        """How many applications actually exist, independent of the page
        limit — so a truncated list can say so instead of presenting the
        newest N as the whole set."""
        if status:
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM driver_applications "
                " WHERE account_id = ? AND status = ?",
                (account_id, status),
            )
        else:
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM driver_applications WHERE account_id = ?",
                (account_id,),
            )
        row = await cur.fetchone()
        return int((row[0] if row else 0) or 0)

    async def list_driver_applications(
        self, account_id: int, *, status: str = "", limit: int = 100,
    ) -> list[dict]:
        """List applications for the recruiter dashboard (no PII blobs).

        Returns the promoted columns + status + dates — NOT the encrypted
        PII or full JSON (the detail endpoint loads those on demand).
        """
        params: list = [account_id]
        where = "WHERE a.account_id = ?"
        if status:
            where += " AND a.status = ?"
            params.append(status)
        params.append(limit)
        # ``duplicate`` flags a re-applicant: another application in THIS
        # account sharing the SSN blind-index, email, or phone.  Detection
        # is recruiter-side only — it never blocked the public submission.
        cur = await self._db.execute(
            f"""SELECT a.id, a.reference, a.status, a.first_name, a.last_name, a.email,
                       a.phone, a.city, a.state, a.cdl_state, a.cdl_class,
                       a.position_type, a.years_cdl, a.submitted_at,
                       EXISTS(
                         SELECT 1 FROM driver_applications b
                          WHERE b.account_id = a.account_id AND b.id <> a.id
                            AND ((a.ssn_hash IS NOT NULL AND b.ssn_hash = a.ssn_hash)
                                 OR (a.email <> '' AND LOWER(b.email) = LOWER(a.email))
                                 OR (a.phone <> '' AND b.phone = a.phone))
                       ) AS duplicate
                  FROM driver_applications a {where}
                 ORDER BY a.id DESC LIMIT ?""",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_stale_approved_applications(
        self, account_id: int, *, older_than_days: int = 3, limit: int = 50,
    ) -> list[dict]:
        """Approved applicants nobody has onboarded yet, waiting too long.

        The failure mode the recruiting↔onboarding split creates: two
        people now own the two halves, so a hand-off can stall silently.
        Rows whose ``reviewed_at`` is blank (approved before the column
        existed) are never nudged — an unknown wait isn't a late one.
        """
        import datetime as _dt
        cutoff = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=older_than_days)
        ).isoformat()
        cur = await self._db.execute(
            """SELECT id, reference, first_name, last_name, email, reviewed_at
                 FROM driver_applications
                WHERE account_id = ? AND status = 'approved'
                  AND reviewed_at <> '' AND reviewed_at < ?
                ORDER BY reviewed_at ASC LIMIT ?""",
            (account_id, cutoff, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def find_duplicate_applications(self, account_id: int, app_id: int) -> list[dict]:
        """Other applications in this account that share the given one's SSN
        (blind index), email, or phone — the recruiter 're-applicant' panel.
        Lightweight rows only (no PII blobs); account-scoped."""
        cur = await self._db.execute(
            "SELECT ssn_hash, email, phone FROM driver_applications WHERE id = ? AND account_id = ?",
            (app_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return []
        d = dict(row)
        ssn_hash, email, phone = d.get("ssn_hash"), (d.get("email") or ""), (d.get("phone") or "")
        # Build only the clauses we have a value for — a bare ``? IS NOT NULL``
        # is type-ambiguous to the driver, and skipping empty signals keeps
        # the match precise.
        clauses: list[str] = []
        params: list = [account_id, app_id]
        if ssn_hash:
            clauses.append("ssn_hash = ?")
            params.append(ssn_hash)
        if email:
            clauses.append("LOWER(email) = LOWER(?)")
            params.append(email)
        if phone:
            clauses.append("phone = ?")
            params.append(phone)
        if not clauses:
            return []
        cur = await self._db.execute(
            f"""SELECT id, reference, status, first_name, last_name, submitted_at
                  FROM driver_applications
                 WHERE account_id = ? AND id <> ? AND ({" OR ".join(clauses)})
                 ORDER BY id DESC""",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_application_status_public(self, reference: str, email: str) -> Optional[dict]:
        """Self-service status lookup — TWO-FACTOR (reference + email), no
        account needed.  Returns minimal {status, submitted_at} or None;
        the caller rate-limits + returns a uniform 'not found' so this is
        not an enumeration oracle.  No PII beyond what the applicant
        already supplied to find the row."""
        ref = (reference or "").strip().upper()
        em = (email or "").strip()
        if not ref or not em:
            return None
        cur = await self._db.execute(
            "SELECT status, submitted_at FROM driver_applications "
            "WHERE reference = ? AND LOWER(email) = LOWER(?) LIMIT 1",
            (ref, em),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_driver_application(
        self, account_id: int, app_id: int, *, decrypt_pii: bool = True,
    ) -> Optional[dict]:
        """Full application detail (account-scoped).  Decrypts DOB/SSN for
        the recruiter view unless ``decrypt_pii`` is False."""
        cur = await self._db.execute(
            "SELECT * FROM driver_applications WHERE id = ? AND account_id = ?",
            (app_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d.pop("ssn_hash", None)  # internal match index — never expose it
        from infra.crypto import decrypt

        def _dj(col: str, default):
            """Pop, (decrypt if sensitive), JSON-load a column."""
            raw = d.pop(col, None) or ""
            if col in _ENCRYPTED_JSON_COLS and raw:
                try:
                    raw = decrypt(raw)
                except Exception:
                    # Key missing/rotated for an encrypted blob — fail safe
                    # (hide rather than surface ciphertext) rather than raise.
                    return default
            try:
                return json.loads(raw) if raw else default
            except (ValueError, TypeError):
                return default

        # Expand JSON columns into objects for the API.
        for k in ("gate_json", "personal_json", "cdl_json", "experience_json",
                  "incidents_json", "position_json", "consents_json", "docs_json",
                  "vetting_json"):
            d[k.replace("_json", "")] = _dj(k, {})
        for k in ("address_history_json", "employment_json"):
            d[k.replace("_json", "")] = _dj(k, [])
        if decrypt_pii:
            from infra.crypto import decrypt
            for col, out in (("dob_enc", "dob"), ("ssn_enc", "ssn")):
                raw = d.pop(col, None)
                try:
                    d[out] = decrypt(raw) if raw else ""
                except Exception:
                    d[out] = ""
        else:
            d.pop("dob_enc", None)
            d.pop("ssn_enc", None)
        return d

    async def update_application_status(
        self, account_id: int, app_id: int, status: str,
        *, reviewed_by: int | None = None, expect_status: str | None = None,
    ) -> bool:
        """Set the status.  With ``expect_status`` the UPDATE is CONDITIONAL
        on the current status (an atomic compare-and-set) and returns False
        if it didn't match — the hire flow uses this so two concurrent
        converts can't both claim one applicant."""
        now = self._now()
        if expect_status is not None:
            cur = await self._db.execute(
                "UPDATE driver_applications SET status = ?, reviewed_by = ?, "
                "reviewed_at = ? WHERE id = ? AND account_id = ? AND status = ?",
                (status, reviewed_by, now, app_id, account_id, expect_status),
            )
        else:
            cur = await self._db.execute(
                "UPDATE driver_applications SET status = ?, reviewed_by = ?, "
                "reviewed_at = ? WHERE id = ? AND account_id = ?",
                (status, reviewed_by, now, app_id, account_id),
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def set_application_notes(
        self, account_id: int, app_id: int, notes: str,
    ) -> bool:
        cur = await self._db.execute(
            "UPDATE driver_applications SET recruiter_notes = ? "
            "WHERE id = ? AND account_id = ?",
            (notes, app_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_application_vetting(
        self, account_id: int, app_id: int, check_key: str, done: bool,
        by_user_id: int | None,
    ) -> dict | None:
        """Toggle one pre-hire check (PSP / MVR / Clearinghouse / …).

        Stamps who ran it + when for the audit trail.  Returns the updated
        vetting dict (``{key: {done, by, at}}``), or None if not found.
        """
        cur = await self._db.execute(
            "SELECT vetting_json FROM driver_applications WHERE id = ? AND account_id = ?",
            (app_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        raw = (dict(row).get("vetting_json") or "")
        try:
            vetting = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            vetting = {}
        if done:
            vetting[check_key] = {"done": True, "by": by_user_id, "at": self._now()}
        else:
            vetting.pop(check_key, None)
        await self._db.execute(
            "UPDATE driver_applications SET vetting_json = ? WHERE id = ? AND account_id = ?",
            (json.dumps(vetting), app_id, account_id),
        )
        await self._db.commit()
        return vetting

    async def mark_application_converted(
        self, account_id: int, app_id: int, user_id: int,
    ) -> bool:
        cur = await self._db.execute(
            "UPDATE driver_applications SET status = 'hired', "
            "converted_to_user_id = ? WHERE id = ? AND account_id = ?",
            (user_id, app_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── In-app notifications + per-user channel prefs ───────────────

    # Canonical channel set; a prefs row stores a comma-list subset.
    NOTIFY_CHANNELS = ("telegram", "email", "dashboard")

    # RETIRED STORE.  ``application_notifications`` is no longer written
    # or read by the product: recruiting notices live in the shared
    # ``notification_inbox`` so one read-state serves both bells (see
    # features/applications/notifications.py).  The table and this
    # writer survive only to keep the ON DELETE CASCADE from
    # driver_applications pinned by test until the table is dropped in
    # its own migration — existing rows stay readable in the database,
    # and the 60-day inbox prune governs the new ones.

    async def create_application_notification(
        self, account_id: int, user_id: int, *, application_id: int | None,
        reference: str, title: str, body: str = "",
        kind: str = "application_submitted",
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO application_notifications
                 (account_id, user_id, application_id, reference, kind,
                  title, body, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (account_id, user_id, application_id, reference, kind, title, body, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_application_notifications(
        self, account_id: int, user_id: int, *, unread_only: bool = False, limit: int = 50,
    ) -> list[dict]:
        where = "WHERE account_id = ? AND user_id = ?"
        params: list = [account_id, user_id]
        if unread_only:
            where += " AND is_read = 0"
        params.append(limit)
        cur = await self._db.execute(
            f"""SELECT id, application_id, reference, kind, title, body, is_read, created_at
                  FROM application_notifications {where}
                 ORDER BY id DESC LIMIT ?""",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_application_notify_channels(self, user_id: int) -> set[str]:
        """A user's chosen channels.  No row → all channels (the default
        for someone who can already review applications)."""
        cur = await self._db.execute(
            "SELECT channels FROM application_notify_prefs WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return set(self.NOTIFY_CHANNELS)
        raw = (dict(row)["channels"] or "").strip()
        return {c.strip() for c in raw.split(",") if c.strip() in self.NOTIFY_CHANNELS}

    async def set_application_notify_channels(
        self, account_id: int, user_id: int, channels: list[str],
    ) -> set[str]:
        clean = [c for c in self.NOTIFY_CHANNELS if c in set(channels)]
        value = ",".join(clean)
        now = self._now()
        await self._db.execute(
            """INSERT INTO application_notify_prefs (user_id, account_id, channels, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET channels = excluded.channels,
                                                  updated_at = excluded.updated_at""",
            (user_id, account_id, value, now),
        )
        await self._db.commit()
        return set(clean)
