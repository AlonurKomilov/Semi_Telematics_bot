"""Carrier Knowledge Base storage — the recruiter-facing directory of the
external carriers an account recruits for.

Each row is one carrier's reference profile.  The large, per-carrier-varying
content (pre-qualification criteria, the presentation/sales sheet, process
notes) is kept as a JSON string in ``content`` so a ``recruiter_manager`` can
fill / skip / add fields without a schema migration — the storage layer never
interprets it.  Deliberately separate from ``companies`` (which owns apply-form
branding); an account's external-carrier directory lives entirely here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class CarrierDirectoryMixin(_MixinBase):
    """CRUD for account-scoped carrier reference profiles.

    Every method takes ``account_id`` as the first argument and scopes its
    SQL to it (tenant isolation), mirroring the other account-scoped mixins.
    Reads return plain dicts for direct JSON serialisation.
    """

    async def list_carrier_profiles(self, account_id: int) -> list[dict]:
        """Directory rows (no ``content`` — the list view only needs the
        name + the one-line experience summary), A→Z by name.  Intake state
        (expiry + review flag) rides along for the list badges; the token
        itself never leaves through here."""
        cur = await self._db.execute(
            "SELECT id, name, website, video_url, experience_summary, "
            "       intake_expires_at, intake_submitted_at, intake_review_pending, "
            "       intake_email, intake_email_sent_at, "
            "       created_at, updated_at "
            "  FROM carrier_profile "
            " WHERE account_id = ? "
            " ORDER BY LOWER(name)",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_carrier_profile(self, account_id: int, profile_id: int) -> dict | None:
        """One full profile (incl. the raw ``content`` JSON) or ``None``."""
        cur = await self._db.execute(
            "SELECT * FROM carrier_profile WHERE account_id = ? AND id = ?",
            (account_id, profile_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_carrier_profile(
        self, account_id: int, *, name: str, website: str = "",
        video_url: str = "", experience_summary: str = "",
        content: str = "{}", created_by: int = 0,
    ) -> dict:
        """Insert a new carrier profile; returns the full stored row."""
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO carrier_profile "
            "(account_id, name, website, video_url, experience_summary, "
            " content, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, name, website, video_url, experience_summary,
             content, created_by, now, now),
        )
        await self._db.commit()
        created = await self.get_carrier_profile(account_id, cur.lastrowid)
        assert created is not None  # just inserted
        return created

    async def update_carrier_profile(
        self, account_id: int, profile_id: int, **fields,
    ) -> None:
        """Patch an existing profile.  Only whitelisted columns are writable;
        ``updated_at`` is stamped whenever anything changes."""
        allowed = {"name", "website", "video_url", "experience_summary", "content"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return
        sets["updated_at"] = self._now()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        await self._db.execute(
            f"UPDATE carrier_profile SET {assignments} "
            "WHERE account_id = ? AND id = ?",
            (*sets.values(), account_id, profile_id),
        )
        await self._db.commit()

    async def delete_carrier_profile(self, account_id: int, profile_id: int) -> None:
        await self._db.execute(
            "DELETE FROM carrier_profile WHERE account_id = ? AND id = ?",
            (account_id, profile_id),
        )
        await self._db.commit()

    # ── Carrier self-fill intake links ──────────────────────────────
    #
    # One active tokenized link per carrier profile (minting again rotates
    # the token).  The external carrier uses it on the public apply host to
    # fill in their own requirements/presentation sheet — see
    # features/carrier_directory/router.py for the gates around these.

    async def set_carrier_intake(
        self, account_id: int, profile_id: int, *,
        token: str, expires_at: str, email: str = "", invited_by: int = 0,
        display_name: str = "",
    ) -> bool:
        """Attach (or rotate) the profile's intake link.  Account-scoped.

        Writes every intake column, so a re-mint never inherits half of
        the previous link's state — ``display_name`` included."""
        cur = await self._db.execute(
            "UPDATE carrier_profile SET intake_token = ?, intake_expires_at = ?, "
            "intake_email = ?, intake_invited_by = ?, intake_display_name = ?, "
            "intake_email_sent_at = '', intake_reminded_at = '', "
            "intake_expiry_warned_at = '', "
            "updated_at = ? WHERE account_id = ? AND id = ?",
            (token, expires_at, email, invited_by, display_name, self._now(),
             account_id, profile_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_carrier_intake(
        self, account_id: int, profile_id: int, **fields,
    ) -> bool:
        """Edit a LIVE intake link in place, keeping its token.

        Exists so fixing the sender name (or expiry, or contact) doesn't
        force a re-mint — rotating the token would break a link the
        carrier has already been emailed.  Only writable when a token is
        actually present, so this can never resurrect a revoked link.

        All three columns are ``NOT NULL DEFAULT ''`` / a set expiry, so
        "not provided" is plain ``None`` and '' is a real clear — no
        sentinel needed.
        """
        allowed = {"intake_display_name", "intake_email", "intake_expires_at"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return False
        sets["updated_at"] = self._now()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        cur = await self._db.execute(
            f"UPDATE carrier_profile SET {assignments} "
            "WHERE account_id = ? AND id = ? AND intake_token != ''",
            (*sets.values(), account_id, profile_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def revoke_carrier_intake(self, account_id: int, profile_id: int) -> None:
        """Kill the profile's intake link (the public form 404s from now on).

        Clears the per-link sender name too — it described THAT link, and
        a later re-mint states its own."""
        await self._db.execute(
            "UPDATE carrier_profile SET intake_token = '', intake_expires_at = NULL, "
            "intake_email = '', intake_display_name = '', intake_email_sent_at = '', "
            "intake_reminded_at = '', intake_expiry_warned_at = '' "
            "WHERE account_id = ? AND id = ?",
            (account_id, profile_id),
        )
        await self._db.commit()

    async def resolve_carrier_intake(self, token: str) -> dict | None:
        """Public token → the full profile row, or ``None`` when the token
        is unknown, revoked, or expired.  A ``None`` must surface as a
        uniform 404 (no oracle for which tokens exist).  Expiry is a text
        compare — both sides are ``_now()``-style ISO strings."""
        if not token:
            return None
        cur = await self._db.execute(
            "SELECT * FROM carrier_profile "
            "WHERE intake_token = ? AND intake_token != '' "
            "AND (intake_expires_at IS NULL OR intake_expires_at > ?)",
            (token, self._now()),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def submit_carrier_intake(
        self, profile_id: int, *, website: str, video_url: str,
        experience_summary: str, content: str,
    ) -> None:
        """Store a carrier's self-fill submission and flag it for manager
        review.  ``content`` is the pre-merged JSON (the router preserves
        the recruiter-only section — a carrier can never see or write it).
        The link stays live until expiry so the carrier can revise."""
        now = self._now()
        await self._db.execute(
            "UPDATE carrier_profile SET website = ?, video_url = ?, "
            "experience_summary = ?, content = ?, intake_submitted_at = ?, "
            "intake_review_pending = 1, updated_at = ? WHERE id = ?",
            (website, video_url, experience_summary, content, now, now, profile_id),
        )
        await self._db.commit()

    async def clear_carrier_intake_review(
        self, account_id: int, profile_id: int,
    ) -> None:
        """Drop the review-pending flag — a manager saved the profile, or
        explicitly marked it reviewed without changing anything."""
        await self._db.execute(
            "UPDATE carrier_profile SET intake_review_pending = 0 "
            "WHERE account_id = ? AND id = ?",
            (account_id, profile_id),
        )
        await self._db.commit()

    async def mark_carrier_intake_emailed(self, profile_id: int) -> None:
        """Stamp a CONFIRMED relay hand-off.  Called only when the mailer
        returned True, so the UI can distinguish "we emailed them" from
        "a manager typed an address and the send failed"."""
        await self._db.execute(
            "UPDATE carrier_profile SET intake_email_sent_at = ? WHERE id = ?",
            (self._now(), profile_id),
        )
        await self._db.commit()

    # ── Nightly lifecycle sweep ─────────────────────────────────────
    #
    # Cross-account by design: the scheduler runs once for the platform and
    # fans out per row.  Both queries are one-shot per link — the marker
    # column is set as the mail goes out, so a retry can never double-send.

    async def list_intakes_needing_reminder(
        self, *, now_iso: str, older_than_iso: str,
    ) -> list[dict]:
        """Live, emailed, still-unfilled links whose invite went out before
        ``older_than_iso`` and that haven't been reminded yet."""
        cur = await self._db.execute(
            "SELECT id, account_id, name, intake_email, intake_expires_at, "
            "       intake_display_name "
            "  FROM carrier_profile "
            " WHERE intake_token != '' "
            "   AND intake_expires_at > ? "
            "   AND intake_email != '' "
            "   AND intake_email_sent_at != '' "
            "   AND intake_email_sent_at < ? "
            "   AND intake_submitted_at = '' "
            "   AND intake_reminded_at = '' "
            " ORDER BY id",
            (now_iso, older_than_iso),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_intakes_expiring_soon(
        self, *, now_iso: str, before_iso: str,
    ) -> list[dict]:
        """Live, still-unfilled links lapsing inside the warning window that
        no manager has been warned about yet."""
        cur = await self._db.execute(
            "SELECT id, account_id, name, intake_email, intake_expires_at "
            "  FROM carrier_profile "
            " WHERE intake_token != '' "
            "   AND intake_expires_at > ? "
            "   AND intake_expires_at <= ? "
            "   AND intake_submitted_at = '' "
            "   AND intake_expiry_warned_at = '' "
            " ORDER BY intake_expires_at",
            (now_iso, before_iso),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_carrier_profile_by_id(self, profile_id: int) -> dict | None:
        """Unscoped fetch — ONLY for the platform-wide nightly sweep, which
        has no user and no account context.  Every request-path read must
        use ``get_carrier_profile`` so tenant isolation is enforced."""
        cur = await self._db.execute(
            "SELECT * FROM carrier_profile WHERE id = ?", (profile_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def stamp_intake_marker(self, profile_id: int, column: str) -> None:
        """Set one sweep marker.  ``column`` is checked against a literal
        allow-list — it is the only part of this SQL that isn't a bound
        parameter, and it must never come from request data."""
        if column not in ("intake_reminded_at", "intake_expiry_warned_at"):
            raise ValueError(f"Not a sweep marker column: {column!r}")
        await self._db.execute(
            f"UPDATE carrier_profile SET {column} = ? WHERE id = ?",
            (self._now(), profile_id),
        )
        await self._db.commit()
