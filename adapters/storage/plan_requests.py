"""Customers asking for a plan that is not sold self-serve.

A plan offered at no price is a "talk to us" plan — Enterprise is the
case this exists for. Its button used to be inert, so the customer who
wanted the biggest thing we sell had nowhere to say so, and the operator
never learned they had asked.

The rules that live here rather than in a route:

- **One open request per (account, tier).** Pressing the button twice
  joins the request already made; a partial unique index enforces it,
  and the insert catches the violation rather than racing.
- **The case number is derived from the id**, so it is unique without a
  second sequence and means something to a person reading an email.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

OPEN, CONTACTED, CLOSED = "open", "contacted", "closed"
STATUSES = (OPEN, CONTACTED, CLOSED)


def case_number(request_id: int, created_at: str) -> str:
    """``4T-202609-0007`` — the string a customer quotes back at us.

    Month-stamped so an operator reading one knows roughly when it came
    in, and id-derived so two requests can never collide.
    """
    stamp = (created_at or "")[:7].replace("-", "") or datetime.now(timezone.utc).strftime("%Y%m")
    return f"4T-{stamp}-{int(request_id):04d}"


class PlanRequestsMixin:
    """Mixed into the platform Database beside the other billing stores."""

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def create_plan_request(
        self, account_id: int, tier: str, *,
        requested_by: Optional[int] = None,
        contact_email: str = "",
        note: str = "",
    ) -> dict:
        """Open a request, or hand back the one already open.

        Returns the row either way, with ``joined`` telling the caller
        which happened — the customer should be told "we already have
        this" rather than being given a second case number for the same
        conversation.
        """
        existing = await self.open_plan_request(account_id, tier)
        if existing:
            return {**existing, "joined": True}
        now = self._now()
        try:
            cur = await self._db.execute(
                "INSERT INTO plan_requests (account_id, tier, requested_by, contact_email, "
                "note, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, tier, requested_by, contact_email.strip(),
                 note.strip(), OPEN, now, now),
            )
        except Exception:
            # The unique index refused it: someone pressed twice fast
            # enough to race the read above. Their request exists, which
            # is the outcome they wanted.
            existing = await self.open_plan_request(account_id, tier)
            if existing:
                return {**existing, "joined": True}
            raise
        request_id = int(getattr(cur, "lastrowid", 0) or 0)
        if not request_id:
            row = await self.open_plan_request(account_id, tier)
            request_id = int(row["id"]) if row else 0
        number = case_number(request_id, now)
        await self._db.execute(
            "UPDATE plan_requests SET case_number = ? WHERE id = ?", (number, request_id))
        row = await self.get_plan_request(request_id)
        return {**(row or {}), "joined": False}

    async def open_plan_request(self, account_id: int, tier: str) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM plan_requests WHERE account_id = ? AND tier = ? AND status = ?",
            (account_id, tier, OPEN),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_plan_request(self, request_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM plan_requests WHERE id = ?", (request_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def plan_requests_for_account(self, account_id: int) -> list[dict]:
        """What this account has asked for — the card shows its own
        case number instead of offering the button again."""
        cur = await self._db.execute(
            "SELECT * FROM plan_requests WHERE account_id = ? AND status <> ? "
            "ORDER BY created_at DESC", (account_id, CLOSED),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_plan_requests(self, *, status: str = "", limit: int = 100) -> list[dict]:
        """The operator's queue, newest first."""
        if status:
            cur = await self._db.execute(
                "SELECT * FROM plan_requests WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, int(limit)),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM plan_requests ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in await cur.fetchall()]

    async def count_open_plan_requests(self) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM plan_requests WHERE status = ?", (OPEN,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_plan_request_status(
        self, request_id: int, status: str, *, actor: str = "",
    ) -> bool:
        """Move a request along. Closing it frees the (account, tier)
        pair, so the customer can ask again later."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        cur = await self._db.execute(
            "UPDATE plan_requests SET status = ?, handled_by = ?, handled_at = ?, "
            "updated_at = ? WHERE id = ?",
            (status, actor, self._now(), self._now(), request_id),
        )
        return bool(getattr(cur, "rowcount", 0))

    _db: Any
