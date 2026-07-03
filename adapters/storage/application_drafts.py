"""Save-and-resume drafts for the public apply form.

One row per (recruiting link, applicant email).  The in-progress form JSON
is stored ENCRYPTED (``data_encrypted`` — it's pre-consent PII); the clear
columns are strictly the fields recruiters may see in their "In progress"
list (name, step progress, timestamps).  ``resume_token`` is the emailed
cross-device unlock; ``draft_secret`` is the in-session write credential so
nobody can overwrite someone else's draft by guessing their email.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class ApplicationDraftsMixin(_MixinBase):
    """CRUD for apply-form drafts — account-scoped, short-lived."""

    async def upsert_application_draft(
        self, account_id: int, *, link_token: str, email: str,
        draft_secret: str | None, first_name: str = "", last_name: str = "",
        step: int = 0, steps_total: int = 0, data_encrypted: str = "",
    ) -> dict | None:
        """Create or update the draft for (link, email).

        On create, mints fresh ``resume_token`` + ``draft_secret``.  On
        update, the caller's ``draft_secret`` must match the stored one —
        returns ``None`` on mismatch (the router turns that into a 403) so a
        stranger who knows an applicant's email can't clobber their draft.
        Returns ``{id, resume_token, draft_secret, created}``.
        """
        email = email.strip().lower()
        cur = await self._db.execute(
            "SELECT id, draft_secret, resume_token FROM application_drafts "
            "WHERE account_id = ? AND link_token = ? AND email = ?",
            (account_id, link_token, email),
        )
        row = await cur.fetchone()
        now = self._now()
        if row:
            if not draft_secret or draft_secret != row["draft_secret"]:
                return None
            await self._db.execute(
                "UPDATE application_drafts SET first_name = ?, last_name = ?, "
                "step = ?, steps_total = ?, data_encrypted = ?, updated_at = ? "
                "WHERE id = ?",
                (first_name, last_name, step, steps_total, data_encrypted,
                 now, row["id"]),
            )
            await self._db.commit()
            return {"id": row["id"], "resume_token": row["resume_token"],
                    "draft_secret": row["draft_secret"], "created": False}
        resume_token = secrets.token_urlsafe(24)
        new_secret = secrets.token_urlsafe(18)
        cur = await self._db.execute(
            "INSERT INTO application_drafts "
            "(account_id, link_token, email, resume_token, draft_secret, "
            " first_name, last_name, step, steps_total, data_encrypted, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, link_token, email, resume_token, new_secret,
             first_name, last_name, step, steps_total, data_encrypted, now, now),
        )
        await self._db.commit()
        return {"id": cur.lastrowid, "resume_token": resume_token,
                "draft_secret": new_secret, "created": True}

    async def get_application_draft_by_secret(
        self, account_id: int, *, link_token: str, email: str, draft_secret: str,
    ) -> dict | None:
        """Read-only ownership check: the row for (link, email) IF the caller
        holds its write secret.  Used by send-link so proving ownership never
        touches the draft body."""
        cur = await self._db.execute(
            "SELECT * FROM application_drafts "
            "WHERE account_id = ? AND link_token = ? AND email = ? AND draft_secret = ?",
            (account_id, link_token, email.strip().lower(), draft_secret),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_application_draft_by_resume(
        self, resume_token: str, email: str,
    ) -> dict | None:
        """The cross-device unlock: resume token AND the matching email
        (re-entered by the applicant) are both required."""
        cur = await self._db.execute(
            "SELECT * FROM application_drafts "
            "WHERE resume_token = ? AND email = ?",
            (resume_token, email.strip().lower()),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_application_draft(
        self, account_id: int, draft_id: int,
    ) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM application_drafts WHERE account_id = ? AND id = ?",
            (account_id, draft_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_application_drafts(self, account_id: int) -> list[dict]:
        """Recruiter list — deliberately excludes ``data_encrypted`` (the
        draft body is pre-consent PII recruiters must not read) and the
        tokens.  Email is returned raw here; the API masks it."""
        cur = await self._db.execute(
            "SELECT d.id, d.link_token, d.email, d.first_name, d.last_name, "
            "       d.step, d.steps_total, d.created_at, d.updated_at, "
            "       d.link_emailed_at, d.reminder_sent_at, l.label AS link_label "
            "  FROM application_drafts d "
            "  LEFT JOIN application_links l ON l.token = d.link_token "
            " WHERE d.account_id = ? "
            " ORDER BY d.updated_at DESC",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_draft_emailed(
        self, draft_id: int, *, reminder: bool = False, auto: bool = False,
    ) -> None:
        """Stamp a sent email.  ``reminder`` writes the shared throttle
        timestamp; ``auto`` additionally bumps the lifetime counter that the
        per-link ``remind_max`` cap counts (manual recruiter nudges don't
        consume the cap — they're a deliberate act)."""
        if reminder and auto:
            await self._db.execute(
                "UPDATE application_drafts SET reminder_sent_at = ?, "
                "reminders_sent = reminders_sent + 1 WHERE id = ?",
                (self._now(), draft_id),
            )
        else:
            col = "reminder_sent_at" if reminder else "link_emailed_at"
            await self._db.execute(
                f"UPDATE application_drafts SET {col} = ? WHERE id = ?",
                (self._now(), draft_id),
            )
        await self._db.commit()

    async def list_drafts_needing_reminder(self, limit: int = 200) -> list[dict]:
        """Drafts due an AUTOMATIC reminder under their link's policy.

        A draft qualifies when ALL hold:
          • its link opted in (``remind_every_hours > 0``), is active, and
            hasn't expired;
          • the draft has been idle for at least the link's cadence;
          • the last reminder (if any) is at least one cadence ago — the
            shared timestamp also spaces us off manual recruiter nudges;
          • the lifetime auto-counter is under the link's ``remind_max``.

        Timestamps are TEXT isoformat, so the cadence math happens in
        Python: the SQL over-selects candidates cheaply (opted-in links,
        counter under cap) and the caller-facing filter runs here.
        """
        from datetime import datetime, timedelta, timezone
        cur = await self._db.execute(
            """
            SELECT d.*, l.remind_every_hours, l.remind_max,
                   l.expires_at AS link_expires_at,
                   co.display_name AS company_name, co.code AS company_code
              FROM application_drafts d
              JOIN application_links l ON l.token = d.link_token
              LEFT JOIN companies co ON co.id = l.company_id
             WHERE l.remind_every_hours > 0
               AND l.is_active = 1
               AND d.reminders_sent < l.remind_max
             ORDER BY d.updated_at
             LIMIT ?
            """,
            (int(limit) * 3,),   # headroom: the Python-side filter narrows
        )
        rows = [dict(r) for r in await cur.fetchall()]

        def _ts(v: object):
            try:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None

        now = datetime.now(timezone.utc)
        due: list[dict] = []
        for r in rows:
            cadence = timedelta(hours=int(r["remind_every_hours"]))
            exp = _ts(r.get("link_expires_at")) if r.get("link_expires_at") else None
            if exp is not None and now > exp:
                continue                      # dead link — never nudge into it
            updated = _ts(r.get("updated_at"))
            if updated is None or now - updated < cadence:
                continue                      # not idle long enough
            last = _ts(r.get("reminder_sent_at")) if r.get("reminder_sent_at") else None
            if last is not None and now - last < cadence:
                continue                      # spaced off the previous nudge
            due.append(r)
            if len(due) >= limit:
                break
        return due

    async def delete_application_draft(
        self, account_id: int, *, link_token: str, email: str,
    ) -> None:
        """Called on successful submit — the draft has served its purpose."""
        await self._db.execute(
            "DELETE FROM application_drafts "
            "WHERE account_id = ? AND link_token = ? AND email = ?",
            (account_id, link_token, email.strip().lower()),
        )
        await self._db.commit()

    async def prune_application_drafts(self, days_keep: int) -> int:
        """Retention executor: drop drafts idle for over ``days_keep`` days
        (platform-wide — drafts are short-lived by design).

        ``updated_at`` is TEXT (``_now()`` isoformat), so the cutoff is
        computed Python-side and compared as text — ISO strings sort
        chronologically, and no SQL date function has to touch the column.
        """
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days_keep))).isoformat()
        cur = await self._db.execute(
            "DELETE FROM application_drafts WHERE updated_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0
