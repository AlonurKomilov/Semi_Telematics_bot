"""AI write-action proposals — the copilot "hands" spine.

The AI never mutates directly: a write tool returns a PROPOSAL persisted
here; the user approves; the approve endpoint EXECUTES after re-checking
permission + scope.  This mixin is only the storage layer.

Security properties baked in here:
  * Scoped by ``(account_id, user_id)`` + an unguessable uuid PK — the
    same platform-DB isolation ``ai_chat_history`` uses (RLS is a
    tenant-DB mechanism; this is the shared platform DB).  Every read/
    mutate takes account_id + user_id and matches on them, so a foreign
    id yields nothing.
  * ``summary`` / ``payload`` / ``result`` are Fernet-encrypted at rest
    (confidentiality — the same infra that protects chat text).
  * ``claim_action_proposal`` is an ATOMIC conditional update
    (pending → executing) so two concurrent approves can't both execute
    the same proposal (advisor fix: no TOCTOU on the burn).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# ``infra.crypto`` imported lazily inside methods — ``infra/__init__``
# imports ``adapters.storage`` back, so a module-level import is a
# circular-import boot failure (same as ai_chat.py).

if TYPE_CHECKING:
    class _MixinBase:
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


def _dec(stored: str) -> str:
    """Best-effort decrypt (a key mismatch degrades to '' rather than
    raising — a dead proposal is safer than a 500)."""
    if not stored:
        return ""
    from infra.crypto import decrypt
    try:
        return decrypt(stored)
    except ValueError:
        return ""


class AIActionProposalsMixin(_MixinBase):

    async def create_action_proposal(
        self, account_id: int, user_id: int, tool: str,
        summary: str, payload_json: str, risk: str = "low",
        *, ttl_minutes: int = 15,
    ) -> str:
        """Persist a proposal; returns its uuid id.  Encrypted at rest."""
        import uuid
        from datetime import datetime, timedelta, timezone
        from infra.crypto import encrypt

        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(minutes=int(ttl_minutes))).isoformat()
        await self._db.execute(
            """INSERT INTO ai_action_proposals
               (id, account_id, user_id, tool, summary, payload, risk,
                status, result, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)""",
            (pid, account_id, user_id, tool,
             encrypt(summary[:2000]), encrypt(payload_json[:8000]),
             risk, now.isoformat(), expires),
        )
        await self._db.commit()
        return pid

    async def get_action_proposal(
        self, proposal_id: str, account_id: int, user_id: int,
    ) -> dict | None:
        """Fetch a proposal, scoped to its owner.  None on any mismatch."""
        cur = await self._db.execute(
            """SELECT id, tool, summary, payload, risk, status, result,
                      created_at, expires_at
                 FROM ai_action_proposals
                WHERE id = ? AND account_id = ? AND user_id = ?""",
            (proposal_id, account_id, user_id),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        return {
            "id": r[0], "tool": r[1],
            "summary": _dec(r[2]), "payload": _dec(r[3]),
            "risk": r[4], "status": r[5], "result": _dec(r[6]),
            "created_at": r[7], "expires_at": r[8],
        }

    async def claim_action_proposal(
        self, proposal_id: str, account_id: int, user_id: int,
    ) -> bool:
        """Atomically claim a pending, unexpired proposal for execution.

        Returns True only if THIS call flipped it ``pending`` →
        ``executing`` — so two concurrent approves can never both proceed
        to the write.  Compares ISO ``expires_at`` lexicographically
        against now (ISO-8601 sorts chronologically).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """UPDATE ai_action_proposals
                  SET status = 'executing'
                WHERE id = ? AND account_id = ? AND user_id = ?
                  AND status = 'pending' AND expires_at > ?""",
            (proposal_id, account_id, user_id, now),
        )
        await self._db.commit()
        return (cur.rowcount or 0) == 1

    async def finalize_action_proposal(
        self, proposal_id: str, account_id: int, user_id: int,
        status: str, result_json: str = "",
    ) -> None:
        """Close a claimed proposal: 'consumed' (+ result) or 'failed'."""
        from infra.crypto import encrypt
        await self._db.execute(
            """UPDATE ai_action_proposals
                  SET status = ?, result = ?
                WHERE id = ? AND account_id = ? AND user_id = ?""",
            (status, encrypt(result_json[:8000]) if result_json else "",
             proposal_id, account_id, user_id),
        )
        await self._db.commit()

    async def decline_action_proposal(
        self, proposal_id: str, account_id: int, user_id: int,
    ) -> bool:
        """User rejected a pending proposal — no write.  Returns whether
        a pending row was actually declined (so a double-reject / stale
        id reports False)."""
        cur = await self._db.execute(
            """UPDATE ai_action_proposals
                  SET status = 'declined'
                WHERE id = ? AND account_id = ? AND user_id = ?
                  AND status = 'pending'""",
            (proposal_id, account_id, user_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) == 1

    async def prune_ai_action_proposals(self, days: int = 7) -> int:
        """Delete proposals older than ``days`` (they're minutes-lived in
        practice; this is the safety sweep).  Runs from the retention
        hub.  Cutoff compares ISO ``created_at`` lexicographically."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        cur = await self._db.execute(
            "DELETE FROM ai_action_proposals WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0
