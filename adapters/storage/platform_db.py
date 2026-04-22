"""Platform database — shared identity/auth tables across all tenants."""

from __future__ import annotations

import logging
import os
from typing import Optional

from .core import _DatabaseCore
from .accounts import AccountsMixin
from .users import UsersMixin
from .invites import InvitesMixin
from .chats import ChatsMixin
from .knowledge_db import KnowledgeBaseMixin
from .permissions_db import PermissionsMixin
from .driver_trucks_db import DriverTrucksMixin
from .user_companies_db import UserCompaniesMixin
from .billing import BillingMixin
from . import platform_schema
from . import platform_migrations

logger = logging.getLogger(__name__)


class PlatformDB(
    AccountsMixin,
    UsersMixin,
    InvitesMixin,
    ChatsMixin,
    KnowledgeBaseMixin,
    PermissionsMixin,
    DriverTrucksMixin,
    UserCompaniesMixin,
    BillingMixin,
    _DatabaseCore,
):
    """SQLite database for platform-wide tables: accounts, users, invites, chats, ai_usage.

    These tables are shared across all tenants and needed for auth/login.
    """

    async def initialize(self):
        """Open DB and create platform schema."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await self._open_connection()
        await platform_schema.create_tables(self._db)
        await platform_migrations.run_all(self._db)

        # Spin up read pool
        for _ in range(self._pool_size):
            conn = await self._open_connection()
            self._all_readers.append(conn)
            self._read_pool.put_nowait(conn)

        logger.info(
            "Platform DB ready at %s (writer + %d readers)",
            self.path, self._pool_size,
        )

    # ── AI usage (lives in platform DB alongside accounts) ────────

    async def log_ai_usage(
        self, account_id: int, user_id: int, model: str,
        request_type: str, prompt_tokens: int = 0,
        reply_tokens: int = 0, total_tokens: int = 0,
        thinking_tokens: int = 0,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO ai_usage
               (account_id, user_id, model, request_type,
                prompt_tokens, reply_tokens, thinking_tokens, total_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, model, request_type,
             prompt_tokens, reply_tokens, thinking_tokens, total_tokens, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_ai_usage_stats(self, account_id: int, days: int = 30) -> dict:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """SELECT request_type, model,
                      COUNT(*) as cnt,
                      SUM(prompt_tokens) as sum_prompt,
                      SUM(reply_tokens) as sum_reply,
                      SUM(COALESCE(thinking_tokens, 0)) as sum_thinking,
                      SUM(total_tokens) as sum_total
               FROM ai_usage
               WHERE account_id = ? AND created_at >= ?
               GROUP BY request_type, model""",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        by_type: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_requests = 0
        total_tokens = 0
        for r in rows:
            rt = r["request_type"] or "unknown"
            m = r["model"] or "unknown"
            cnt = r["cnt"]
            tok = r["sum_total"] or 0
            total_requests += cnt
            total_tokens += tok
            if rt not in by_type:
                by_type[rt] = {"requests": 0, "tokens": 0}
            by_type[rt]["requests"] += cnt
            by_type[rt]["tokens"] += tok
            if m not in by_model:
                by_model[m] = {"requests": 0, "tokens": 0}
            by_model[m]["requests"] += cnt
            by_model[m]["tokens"] += tok
        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "by_type": by_type,
            "by_model": by_model,
            "days": days,
        }

    async def get_ai_usage_daily(self, account_id: int, days: int = 7) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as requests, "
            "SUM(total_tokens) as tokens "
            "FROM ai_usage WHERE account_id = ? AND created_at >= ? "
            "GROUP BY DATE(created_at) ORDER BY day",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_ai_usage_all_accounts(self, days: int = 90) -> dict:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "SELECT COUNT(*) as total_requests, "
            "SUM(total_tokens) as total_tokens, "
            "COUNT(DISTINCT account_id) as active_accounts "
            "FROM ai_usage WHERE created_at >= ?",
            (cutoff,),
        )
        row = await cur.fetchone()
        return dict(row) if row else {}
