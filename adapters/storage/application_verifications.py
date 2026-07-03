"""§391.23 employer-verification trail storage.

One row per (application, prior-employer index) once a recruiter engages
that employer.  The row records where/when requests were sent, how many
attempts, and the outcome — the good-faith documentation FMCSA expects
when an old employer never answers.  Employers the recruiter hasn't
touched yet have NO row (the API derives the candidate list from the
application's employment history and merges these in).
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

VERIFICATION_STATUSES = ("pending", "sent", "received", "no_response")


class ApplicationVerificationsMixin(_MixinBase):
    async def list_employer_verifications(
        self, account_id: int, application_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM application_employer_verifications "
            "WHERE account_id = ? AND application_id = ? "
            "ORDER BY employer_index",
            (account_id, application_id),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def record_verification_sent(
        self, account_id: int, application_id: int, employer_index: int, *,
        employer_name: str, employer_email: str,
    ) -> dict:
        """Upsert the row for this employer as sent-now (attempts += 1)."""
        now = self._now()
        cur = await self._db.execute(
            "SELECT id FROM application_employer_verifications "
            "WHERE application_id = ? AND employer_index = ?",
            (application_id, employer_index),
        )
        row = await cur.fetchone()
        if row:
            await self._db.execute(
                "UPDATE application_employer_verifications SET "
                "employer_name = ?, employer_email = ?, status = 'sent', "
                "attempts = attempts + 1, sent_at = ?, updated_at = ? "
                "WHERE id = ?",
                (employer_name, employer_email, now, now, row["id"]),
            )
            await self._db.commit()
            vid = row["id"]
        else:
            cur = await self._db.execute(
                "INSERT INTO application_employer_verifications "
                "(account_id, application_id, employer_index, employer_name, "
                " employer_email, status, attempts, sent_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'sent', 1, ?, ?, ?)",
                (account_id, application_id, employer_index, employer_name,
                 employer_email, now, now, now),
            )
            await self._db.commit()
            vid = cur.lastrowid
        got = await self._db.execute(
            "SELECT * FROM application_employer_verifications WHERE id = ?", (vid,),
        )
        return dict(await got.fetchone())

    async def update_verification_status(
        self, account_id: int, verification_id: int, *,
        status: str | None = None, notes: str | None = None,
    ) -> bool:
        """Recruiter marks an outcome (received / no_response) and/or notes."""
        sets: list[str] = []
        vals: list[Any] = []
        if status is not None:
            if status not in VERIFICATION_STATUSES:
                return False
            sets.append("status = ?")
            vals.append(status)
            if status == "received":
                sets.append("responded_at = ?")
                vals.append(self._now())
        if notes is not None:
            sets.append("notes = ?")
            vals.append(notes)
        if not sets:
            return False
        sets.append("updated_at = ?")
        vals.append(self._now())
        vals += [verification_id, account_id]
        cur = await self._db.execute(
            f"UPDATE application_employer_verifications SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ?",
            tuple(vals),
        )
        await self._db.commit()
        return cur.rowcount > 0
