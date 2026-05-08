"""Accounts CRUD mixin."""

from __future__ import annotations

import logging
from typing import Optional

from .models import Account

logger = logging.getLogger(__name__)


class AccountsMixin:

    async def create_account(self, name: str, tier: str = "free") -> Account:
        """Create a new subscriber account."""
        now = self._now()
        slug = self._make_slug(name)
        cur = await self._db.execute(
            "INSERT INTO accounts (name, slug, tier, created_at) VALUES (?, ?, ?, ?)",
            (name, slug, tier, now),
        )
        await self._db.commit()
        acct = Account(id=cur.lastrowid, name=name, slug=slug,
                       tier=tier, is_active=True, created_at=now)
        # Seed default role permissions for the new account
        try:
            await self.seed_account_permissions(acct.id)
        except Exception as e:
            logger.warning("Could not seed default permissions for account %d: %s", acct.id, e)
        return acct

    async def get_account(self, account_id: int) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def get_account_by_slug(self, slug: str) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE slug = ?", (slug,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def list_accounts(self, active_only: bool = True) -> list[Account]:
        q = "SELECT * FROM accounts"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY name"
        cur = await self._db.execute(q)
        rows = await cur.fetchall()
        return [self._row_to_account(r) for r in rows]

    async def update_account(self, account_id: int, **kwargs) -> bool:
        """Update account fields. Allowed keys: name, tier, is_active, bot_token_encrypted, bot_username, webhook_secret."""
        allowed = {"name", "tier", "is_active", "bot_token_encrypted", "bot_username", "webhook_secret", "payroll_enabled", "coaching_enabled"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        await self._db.execute(
            f"UPDATE accounts SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()
        return True

    async def update_account_tier(self, account_id: int, tier: str) -> bool:
        """Shortcut to update only the tier column on accounts."""
        return await self.update_account(account_id, tier=tier)

    async def get_accounts_with_bot_tokens(self) -> list[Account]:
        """Return all active accounts that have a bot token configured."""
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE is_active = 1 "
            "AND bot_token_encrypted IS NOT NULL AND bot_token_encrypted != '' "
            "ORDER BY id"
        )
        rows = await cur.fetchall()
        return [self._row_to_account(r) for r in rows]
