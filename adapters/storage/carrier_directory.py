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
    ) -> bool:
        """Attach (or rotate) the profile's intake link.  Account-scoped."""
        cur = await self._db.execute(
            "UPDATE carrier_profile SET intake_token = ?, intake_expires_at = ?, "
            "intake_email = ?, intake_invited_by = ?, updated_at = ? "
            "WHERE account_id = ? AND id = ?",
            (token, expires_at, email, invited_by, self._now(),
             account_id, profile_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def revoke_carrier_intake(self, account_id: int, profile_id: int) -> None:
        """Kill the profile's intake link (the public form 404s from now on)."""
        await self._db.execute(
            "UPDATE carrier_profile SET intake_token = '', intake_expires_at = NULL, "
            "intake_email = '' WHERE account_id = ? AND id = ?",
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
        """Drop the review-pending flag — a manager saved the profile."""
        await self._db.execute(
            "UPDATE carrier_profile SET intake_review_pending = 0 "
            "WHERE account_id = ? AND id = ?",
            (account_id, profile_id),
        )
        await self._db.commit()
