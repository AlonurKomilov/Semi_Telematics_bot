"""Changing the address a customer's bills go to, in two proofs.

The billing contact is the one field on the Billing page a customer can
change, and it is the one field where being wrong is expensive: every
invoice, every receipt and every dunning notice goes there, so an
address typed a character wrong means a customer stops hearing from us
and finds out when the card lapses.  It is also the address an attacker
who reached a session would change first — redirect the paperwork, and
nobody notices the account being taken apart.

So a change is not a write, it is a pair of proofs:

- **The person asking is the owner.**  A six-digit code goes to the
  requester's OWN sign-in address, not to the billing address — that
  half proves the request came from a person who already holds the
  account's email, which a stolen session does not.
- **The new address is real and is theirs.**  A one-time link goes to
  the NEW address and nothing changes until somebody there opens it.
  A typo simply never confirms, and the old address keeps working.

Only then does the address move, locally and on Stripe.  One pending
change per account (UNIQUE on ``account_id``), so asking again replaces
the request rather than leaving two codes alive.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

#: The code was sent to the requester and is waiting to be typed back.
CODE_SENT = "code_sent"
#: The code checked out; the link is with the new address, unopened.
CONFIRM_SENT = "confirm_sent"

#: A code is short-lived because it is short: six digits is 20 bits, and
#: the only thing standing between that and a guess is the window.
CODE_TTL_MINUTES = 15
#: The link is long-lived because the person who must open it may not be
#: the person who asked — an accountant reads their mail tomorrow.
LINK_TTL_HOURS = 48


def _looks_like_email(value: str) -> bool:
    """A cheap shape check, not an RFC.

    The real proof that an address exists is the confirmation link; this
    only catches what a person can see is wrong before we waste a send.
    Private on purpose: ``receipt_email.looks_like_email`` is the one
    public answer to this question, and a second public copy would be
    two names for one rule.
    """
    value = (value or "").strip()
    if len(value) < 3 or len(value) > 320 or value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") \
        and not domain.endswith(".") and " " not in value


def mask_email(value: str) -> str:
    """``ad••@premiertruckinggroup.com`` — enough to recognise, not to read.

    The UI has to say WHERE the code went or the person cannot act on
    it, but a session that should not be looking must not learn the
    owner's full address from the answer.
    """
    value = (value or "").strip()
    if "@" not in value:
        return ""
    local, _, domain = value.partition("@")
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}{'•' * max(2, len(local) - len(head))}@{domain}"


def _hash_code(code: str) -> str:
    """SHA-256 of the typed code.

    Six digits would be trivially scannable in plaintext.  No salt, for
    the same reason the deletion codes carry none: single-use rows,
    scoped to one account, gone in fifteen minutes.
    """
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class BillingEmailChangesMixin:
    """Mixed into the platform Database beside the other billing stores."""

    async def start_billing_email_change(
        self, account_id: int, *, new_email: str, requested_by: int,
        requested_email: str, ttl_minutes: int = CODE_TTL_MINUTES,
    ) -> str:
        """Open a change and return the plaintext code to email.

        Replaces any request already open for this account, so clicking
        "send another code" kills the previous one instead of leaving
        two live.  Only the hash is stored.
        """
        if not _looks_like_email(new_email):
            raise ValueError("That does not look like an email address.")
        if not _looks_like_email(requested_email):
            raise ValueError(
                "Your profile has no email to send the code to — add one in "
                "Profile settings first.")
        code = str(secrets.randbelow(1_000_000)).zfill(6)
        now = _now()
        await self._db.execute(
            """
            INSERT INTO billing_email_changes
                (account_id, new_email, requested_by, requested_email,
                 code_hash, code_expires_at, confirm_token, token_expires_at,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                new_email        = excluded.new_email,
                requested_by     = excluded.requested_by,
                requested_email  = excluded.requested_email,
                code_hash        = excluded.code_hash,
                code_expires_at  = excluded.code_expires_at,
                confirm_token    = '',
                token_expires_at = '',
                status           = excluded.status,
                updated_at       = excluded.updated_at
            """,
            (account_id, new_email.strip(), requested_by,
             requested_email.strip().lower(), _hash_code(code),
             _stamp(now + timedelta(minutes=ttl_minutes)),
             CODE_SENT, _stamp(now), _stamp(now)),
        )
        await self._db.commit()
        return code

    async def verify_billing_email_code(
        self, account_id: int, code: str, *, requested_by: int | None = None,
        ttl_hours: int = LINK_TTL_HOURS,
    ) -> Optional[str]:
        """Check the code; on success mint and return the confirm token.

        Returns None for every failure — wrong, expired, not this
        account's, already past this step — because telling the caller
        WHICH is telling an attacker where they are.  The row is left
        alone on a miss so the owner can retype until the code expires.
        """
        row = await self._get_change_row(account_id)
        if not row or row.get("status") != CODE_SENT:
            return None
        if requested_by is not None and int(row.get("requested_by") or 0) != int(requested_by):
            return None
        if str(row.get("code_expires_at") or "") < _stamp(_now()):
            return None
        if not secrets.compare_digest(
                str(row.get("code_hash") or ""), _hash_code((code or "").strip())):
            return None
        token = secrets.token_urlsafe(32)
        now = _now()
        await self._db.execute(
            """
            UPDATE billing_email_changes
               SET code_hash = '', confirm_token = ?, token_expires_at = ?,
                   status = ?, updated_at = ?
             WHERE account_id = ?
            """,
            (token, _stamp(now + timedelta(hours=ttl_hours)),
             CONFIRM_SENT, _stamp(now), account_id),
        )
        await self._db.commit()
        return token

    async def confirm_billing_email_change(self, token: str) -> Optional[dict]:
        """Redeem the link.  Returns the change, or None.

        Deletes the row on success, so the link works exactly once: the
        caller applies the address, and a second click finds nothing —
        the same answer an unknown token gets, which is the point.
        """
        token = (token or "").strip()
        if not token:
            return None
        cur = await self._db.execute(
            "SELECT * FROM billing_email_changes WHERE confirm_token = ?",
            (token,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("status") != CONFIRM_SENT:
            return None
        if str(row.get("token_expires_at") or "") < _stamp(_now()):
            return None
        await self._db.execute(
            "DELETE FROM billing_email_changes WHERE account_id = ?",
            (int(row["account_id"]),),
        )
        await self._db.commit()
        return {"account_id": int(row["account_id"]),
                "new_email": str(row.get("new_email") or ""),
                "requested_by": int(row.get("requested_by") or 0)}

    async def pending_billing_email_change(self, account_id: int) -> Optional[dict]:
        """What the page shows while a change is in flight — no secrets.

        Neither the code hash nor the token is returned: the page needs
        to say which step it is on and where each message went, and a
        response that carried the token would hand the second proof to
        whoever holds the first.
        """
        row = await self._get_change_row(account_id)
        if not row:
            return None
        return {
            "new_email":    str(row.get("new_email") or ""),
            "status":       str(row.get("status") or ""),
            "code_sent_to": mask_email(str(row.get("requested_email") or "")),
            "expires_at":   str(row.get("code_expires_at") or "")
                            if row.get("status") == CODE_SENT
                            else str(row.get("token_expires_at") or ""),
            "created_at":   str(row.get("created_at") or ""),
        }

    async def cancel_billing_email_change(self, account_id: int) -> bool:
        """Drop a request.  True when there was one to drop."""
        if not await self._get_change_row(account_id):
            return False
        await self._db.execute(
            "DELETE FROM billing_email_changes WHERE account_id = ?", (account_id,),
        )
        await self._db.commit()
        return True

    async def _get_change_row(self, account_id: int) -> Optional[dict[str, Any]]:
        cur = await self._db.execute(
            "SELECT * FROM billing_email_changes WHERE account_id = ?", (account_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


__all__ = [
    "BillingEmailChangesMixin",
    "CODE_SENT", "CONFIRM_SENT",
    "CODE_TTL_MINUTES", "LINK_TTL_HOURS",
    "mask_email",
]
