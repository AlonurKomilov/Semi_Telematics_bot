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
        name + the one-line experience summary), A→Z by name."""
        cur = await self._db.execute(
            "SELECT id, name, website, video_url, experience_summary, "
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
