"""Role-default page layouts — tier two of the page-config model.

One row per (account, role, feature page): the ordered section list a
role MANAGER chose as their team's default.  It replaces the shipped
persona layout as the BASE the frontend resolver starts from; each
user's personal preference still applies on top (Option A — a manager's
setup is a default, not a lock).

This is a feature table, deliberately NOT a permission and NOT a user
preference: it is structured data (a list with order) that affects every
member of a role, which is exactly what the preferences service's own
rule excludes from per-user storage.  WHO may write it is a permission
(``can_manage_config_role`` + the API's own is_manager/role re-check);
WHAT was written lives here.

``sections`` is stored as a JSON array of section ids.  The backend does
not know the frontend's section registry, so it validates SHAPE only
(the API caps counts/lengths); the frontend falls back to the shipped
layout wholesale when a stored default fails its registry validation —
required sections are enforced there, where the registry lives.
"""

import json
from datetime import datetime, timezone
from typing import Optional


class PageLayoutsMixin:
    async def get_page_layouts(self, account_id: int) -> list[dict]:
        """Every role's stored layout for the account.

        Fetched in one query (the whole account's worth is a handful of
        tiny rows) so the frontend can pick by active view without a
        request per persona switch.
        """
        cur = await self._db.execute(
            "SELECT role, feature, sections, updated_by, updated_at"
            "  FROM page_layouts WHERE account_id = ?",
            (account_id,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sections"] = json.loads(d["sections"])
            except (TypeError, ValueError):
                # A row we can't parse is a row that doesn't exist — the
                # frontend then uses the shipped layout, which is the
                # correct degraded behaviour for config.
                continue
            out.append(d)
        return out

    async def upsert_page_layout(
        self, account_id: int, role: str, feature: str,
        sections: list[str], updated_by: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO page_layouts"
            " (account_id, role, feature, sections, updated_by, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (account_id, role, feature) DO UPDATE SET"
            "   sections = excluded.sections,"
            "   updated_by = excluded.updated_by,"
            "   updated_at = excluded.updated_at",
            (account_id, role, feature, json.dumps(sections), updated_by, now),
        )
        await self._db.commit()

    async def delete_page_layout(
        self, account_id: int, role: str, feature: str,
    ) -> Optional[dict]:
        """Remove a role default; the team falls back to the shipped
        layout.  Returns the removed row (or None) so the API can 404
        a delete of something that wasn't there."""
        cur = await self._db.execute(
            "DELETE FROM page_layouts"
            " WHERE account_id = ? AND role = ? AND feature = ?"
            " RETURNING role, feature",
            (account_id, role, feature),
        )
        row = await cur.fetchone()
        await self._db.commit()
        return dict(row) if row else None
