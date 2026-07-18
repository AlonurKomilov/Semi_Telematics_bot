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
        *, ttl_minutes: int = 15, staged_payload_json: str = "",
    ) -> str:
        """Persist a proposal; returns its uuid id.  Encrypted at rest.

        ``staged_payload_json`` carries a bulk action's server-derived
        rows (the exact data the user approves).  Unlike ``payload`` it
        is NOT length-truncated — the executor writes FROM these rows,
        and a silently-shortened import would corrupt the write.
        """
        import uuid
        from datetime import datetime, timedelta, timezone
        from infra.crypto import encrypt

        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(minutes=int(ttl_minutes))).isoformat()
        await self._db.execute(
            """INSERT INTO ai_action_proposals
               (id, account_id, user_id, tool, summary, payload, risk,
                status, result, staged_payload, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?, ?)""",
            (pid, account_id, user_id, tool,
             encrypt(summary[:2000]), encrypt(payload_json[:8000]),
             risk,
             encrypt(staged_payload_json) if staged_payload_json else "",
             now.isoformat(), expires),
        )
        await self._db.commit()
        return pid

    async def get_action_proposal(
        self, proposal_id: str, account_id: int, user_id: int,
    ) -> dict | None:
        """Fetch a proposal, scoped to its owner.  None on any mismatch."""
        cur = await self._db.execute(
            """SELECT id, tool, summary, payload, risk, status, result,
                      created_at, expires_at, staged_payload,
                      user_id, undone_at, undone_by
                 FROM ai_action_proposals
                WHERE id = ? AND account_id = ? AND user_id = ?""",
            (proposal_id, account_id, user_id),
        )
        return self._row_to_proposal(await cur.fetchone())

    async def get_action_proposal_for_account(
        self, proposal_id: str, account_id: int,
    ) -> dict | None:
        """Fetch a proposal scoped to the ACCOUNT only — for the undo
        path, where an owner/admin may reverse another employee's
        executed action.  Callers MUST gate on role before using this
        (the creator-scoped getter stays the default read)."""
        cur = await self._db.execute(
            """SELECT id, tool, summary, payload, risk, status, result,
                      created_at, expires_at, staged_payload,
                      user_id, undone_at, undone_by
                 FROM ai_action_proposals
                WHERE id = ? AND account_id = ?""",
            (proposal_id, account_id),
        )
        return self._row_to_proposal(await cur.fetchone())

    @staticmethod
    def _row_to_proposal(r) -> dict | None:
        if r is None:
            return None
        return {
            "id": r[0], "tool": r[1],
            "summary": _dec(r[2]), "payload": _dec(r[3]),
            "risk": r[4], "status": r[5], "result": _dec(r[6]),
            "created_at": r[7], "expires_at": r[8],
            "staged_payload": _dec(r[9]),
            "user_id": r[10], "undone_at": r[11] or "", "undone_by": r[12],
        }

    async def claim_action_undo(
        self, proposal_id: str, account_id: int,
    ) -> bool:
        """Atomically claim a consumed proposal for undo (consumed →
        undoing) — two concurrent undos can never both run the reverse.
        Authorization happens BEFORE this call (approver or owner/admin);
        scoping here is by account."""
        cur = await self._db.execute(
            """UPDATE ai_action_proposals
                  SET status = 'undoing'
                WHERE id = ? AND account_id = ? AND status = 'consumed'""",
            (proposal_id, account_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) == 1

    async def finalize_action_undo(
        self, proposal_id: str, account_id: int, *,
        success: bool, undone_by: int | None = None,
        result_json: str = "",
    ) -> None:
        """Close an undo claim in ONE statement: 'undone' (stamped
        who/when, result replaced with the merged undo outcome) on
        success, back to 'consumed' on failure so the undo stays
        available.  Single UPDATE on purpose (reviewer): a two-step
        finalize left a window where status said 'undone' but the
        outcome message wasn't stored yet — and a crash between the
        steps lost it forever.  Both branches guard on
        ``status = 'undoing'`` (single-writer symmetry with the claim).
        """
        if success:
            from datetime import datetime, timezone
            from infra.crypto import encrypt
            await self._db.execute(
                """UPDATE ai_action_proposals
                      SET status = 'undone', undone_at = ?, undone_by = ?,
                          result = ?
                    WHERE id = ? AND account_id = ? AND status = 'undoing'""",
                (datetime.now(timezone.utc).isoformat(), undone_by,
                 encrypt(result_json) if result_json else "",
                 proposal_id, account_id),
            )
        else:
            await self._db.execute(
                """UPDATE ai_action_proposals
                      SET status = 'consumed'
                    WHERE id = ? AND account_id = ? AND status = 'undoing'""",
                (proposal_id, account_id),
            )
        await self._db.commit()

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
        """Close a claimed proposal: 'consumed' (+ result) or 'failed'.

        ``result`` is deliberately NOT length-truncated (unlike the
        model-adjacent ``payload``): it is server-built by the executor
        and size-bounded by construction, and bulk actions store their
        undo manifest in it (``_item_ids``) — a silently-shortened
        manifest would corrupt a later undo.
        """
        from infra.crypto import encrypt
        await self._db.execute(
            """UPDATE ai_action_proposals
                  SET status = ?, result = ?
                WHERE id = ? AND account_id = ? AND user_id = ?""",
            (status, encrypt(result_json) if result_json else "",
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
