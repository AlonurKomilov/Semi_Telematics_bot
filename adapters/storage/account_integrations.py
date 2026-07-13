"""Account integrations CRUD mixin.

Persists per-account telematics integration state — one row per
(account, provider) pair in ``account_integrations``.  The credentials
blob is encrypted at rest (Fernet via ``infra.crypto``) and decrypted
only when a client is built.  Feature toggles + cadence overrides are
stored as JSON to keep the schema stable across providers that expose
different capability sets.

Vendor-neutral by design — every method takes ``provider_id`` as a
parameter rather than assuming Samsara.  The scheduler, dashboard
routes, and capabilities layer all go through this mixin instead of
querying the table directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional


if TYPE_CHECKING:
    # The concrete ``Database`` class composes this mixin with
    # ``_DatabaseCore`` which provides the ``_db`` connection pool
    # attribute.  Declared here so mypy can resolve attribute access.
    class _MixinBase:
        _db: Any
else:
    _MixinBase = object

logger = logging.getLogger(__name__)


_INTEGRATION_COLUMNS = (
    "account_id", "provider_id", "status",
    "connected_at", "credentials_enc",
    "feature_toggles", "cadence_overrides",
    "last_health_at", "last_health_error",
    "last_backfill_at",
    "created_by", "created_at", "updated_at",
)


@dataclass
class AccountIntegration:
    """In-memory shape of a single account_integrations row.

    ``credentials`` is the decrypted credentials dict — callers
    receive plaintext.  ``credentials_enc`` is never exposed; the
    row-to-object conversion in ``_row_to_integration`` decrypts and
    drops it.

    ``feature_toggles`` and ``cadence_overrides`` are returned as
    parsed dicts so consumers never have to ``json.loads`` themselves.
    """

    account_id: int
    provider_id: str
    status: str
    connected_at: str
    credentials: dict[str, Any]
    feature_toggles: dict[str, dict[str, Any]]
    cadence_overrides: dict[str, dict[str, Any]]
    last_health_at: str
    last_health_error: str
    last_backfill_at: str
    created_by: int
    created_at: str
    updated_at: str

    capability_history: list[str] = field(default_factory=list)
    """Reserved for future per-capability audit trail; unused today."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_json_loads(blob: str, default: Any) -> Any:
    if not blob:
        return default
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return default


class AccountIntegrationsMixin(_MixinBase):
    """Storage helpers for the account_integrations table."""

    # ── Read path ─────────────────────────────────────────────────

    async def get_account_integration(
        self, account_id: int, provider_id: str,
    ) -> Optional[AccountIntegration]:
        """Return the integration row for (account, provider) or None.

        Credentials are decrypted before return.  A row that fails to
        decrypt (key rotation drift, corrupted blob) is returned with
        ``credentials={}`` and a logged error — callers see "no
        credentials" rather than crashing the request path.
        """
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(_INTEGRATION_COLUMNS)}
              FROM account_integrations
             WHERE account_id = ? AND provider_id = ?
            """,
            (account_id, provider_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_integration(row)

    async def list_account_integrations(
        self, account_id: int,
    ) -> list[AccountIntegration]:
        """All integrations configured for an account, any status."""
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(_INTEGRATION_COLUMNS)}
              FROM account_integrations
             WHERE account_id = ?
             ORDER BY provider_id
            """,
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_integration(r) for r in rows]

    async def list_enabled_account_integrations(
        self, account_id: int, *, include_error: bool = False,
    ) -> list[AccountIntegration]:
        """Integrations the account has actively connected.

        Excludes ``disabled`` / ``disconnected`` rows so the scheduler
        doesn't fire ingest jobs for paused providers.

        ``include_error=True`` additionally returns ``error`` rows — the
        health-check prober needs them so a status that auto-flipped to
        ``error`` can heal back to ``connected`` when the provider
        recovers.  Without this the flip is a one-way trap: the prober
        never re-probes an error row, so one transient upstream failure
        silently freezes every ingest for the account until a human
        notices (that is exactly what stranded a fleet's vehicle_state
        for 5 days).
        """
        statuses = ("connected", "error") if include_error else ("connected",)
        placeholders = ", ".join("?" for _ in statuses)
        cur = await self._db.execute(
            f"""
            SELECT {', '.join(_INTEGRATION_COLUMNS)}
              FROM account_integrations
             WHERE account_id = ? AND status IN ({placeholders})
             ORDER BY provider_id
            """,
            (account_id, *statuses),
        )
        rows = await cur.fetchall()
        return [self._row_to_integration(r) for r in rows]

    # ── Write path ────────────────────────────────────────────────

    async def upsert_account_integration(
        self,
        account_id: int,
        provider_id: str,
        *,
        credentials: dict[str, Any] | None = None,
        feature_toggles: dict[str, dict] | None = None,
        cadence_overrides: dict[str, dict] | None = None,
        status: str = "connected",
        created_by: int = 0,
    ) -> AccountIntegration:
        """Create or update an integration row.

        Treats ``credentials=None`` and ``feature_toggles=None`` as
        "leave existing value untouched" so the dashboard's toggle
        editor doesn't have to re-send the credentials with every
        toggle change.  Passing an empty dict is different — it
        clears the field.
        """
        from infra.crypto import encrypt as _enc

        now = _now_iso()
        existing = await self.get_account_integration(account_id, provider_id)

        # Resolve creds: None → keep existing; dict → encrypt fresh.
        if credentials is None and existing:
            # Re-encrypt existing — equivalent to leaving the row's
            # credentials_enc untouched.  We do a SELECT-then-UPDATE
            # rather than COALESCE so the encryption step is explicit
            # and consistent with the create path.
            creds_to_store = existing.credentials
        else:
            creds_to_store = credentials or {}
        credentials_enc = _enc(json.dumps(creds_to_store, sort_keys=True))

        if feature_toggles is None:
            toggles_to_store = existing.feature_toggles if existing else {}
        else:
            toggles_to_store = feature_toggles

        if cadence_overrides is None:
            cadence_to_store = existing.cadence_overrides if existing else {}
        else:
            cadence_to_store = cadence_overrides

        connected_at = existing.connected_at if existing else now
        created_at = existing.created_at if existing else now
        created_by_to_store = existing.created_by if existing else created_by

        await self._db.execute(
            """
            INSERT INTO account_integrations (
                account_id, provider_id, status,
                connected_at, credentials_enc,
                feature_toggles, cadence_overrides,
                last_health_at, last_health_error,
                last_backfill_at,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, provider_id) DO UPDATE SET
                status=excluded.status,
                credentials_enc=excluded.credentials_enc,
                feature_toggles=excluded.feature_toggles,
                cadence_overrides=excluded.cadence_overrides,
                updated_at=excluded.updated_at
            """,
            (
                account_id, provider_id, status,
                connected_at, credentials_enc,
                json.dumps(toggles_to_store, sort_keys=True),
                json.dumps(cadence_to_store, sort_keys=True),
                existing.last_health_at if existing else "",
                existing.last_health_error if existing else "",
                existing.last_backfill_at if existing else "",
                created_by_to_store, created_at, now,
            ),
        )
        await self._db.commit()
        result = await self.get_account_integration(account_id, provider_id)
        assert result is not None, "upsert race — integration vanished mid-write"
        return result

    async def update_feature_toggles(
        self,
        account_id: int,
        provider_id: str,
        toggles: dict[str, dict[str, Any]],
    ) -> Optional[AccountIntegration]:
        """Replace the feature_toggles dict wholesale.

        Returns None when no integration exists for the pair — the
        caller usually maps that to a 404 on the API surface.  Use
        ``upsert_account_integration`` to create rows.
        """
        existing = await self.get_account_integration(account_id, provider_id)
        if existing is None:
            return None
        return await self.upsert_account_integration(
            account_id, provider_id,
            feature_toggles=toggles,
            status=existing.status,
        )

    async def set_company_credential(
        self,
        account_id: int,
        provider_id: str,
        company_code: str,
        token: Optional[str],
    ) -> Optional[AccountIntegration]:
        """Upsert or remove ONE company's API token inside the
        integration's encrypted credentials map.

        ``token`` is the raw value the operator typed; ``None`` (or
        empty string) deletes the entry — useful for "Remove key" on
        the Integration card.  The credentials blob stays a dict with
        the shape ``{"companies": {code: token}, "api_token": ""}``.

        Other companies' tokens are preserved.  Returns the updated
        ``AccountIntegration`` so the caller can serialize it for the
        client.  Returns None when no integration row exists.
        """
        existing = await self.get_account_integration(account_id, provider_id)
        if existing is None:
            return None
        creds = dict(existing.credentials or {})
        companies = dict(creds.get("companies") or {})
        if token:
            companies[company_code] = token
        else:
            companies.pop(company_code, None)
        creds["companies"] = companies
        creds.setdefault("api_token", "")
        return await self.upsert_account_integration(
            account_id, provider_id,
            credentials=creds,
            status=existing.status,
        )

    async def set_integration_status(
        self,
        account_id: int,
        provider_id: str,
        status: str,
    ) -> Optional[AccountIntegration]:
        """Flip an integration between ``connected`` / ``disabled`` /
        ``error`` / ``disconnected``.  Side-effect-free at this layer;
        the scheduler reads the value on its next tick and applies it.
        """
        existing = await self.get_account_integration(account_id, provider_id)
        if existing is None:
            return None
        now = _now_iso()
        await self._db.execute(
            "UPDATE account_integrations "
            "   SET status = ?, updated_at = ? "
            " WHERE account_id = ? AND provider_id = ?",
            (status, now, account_id, provider_id),
        )
        await self._db.commit()
        return await self.get_account_integration(account_id, provider_id)

    async def record_integration_backfill_completion(
        self,
        account_id: int,
        provider_id: str,
    ) -> None:
        """Stamp ``last_backfill_at`` on a successfully completed M5
        history backfill.

        Surfaces in the dashboard Integrations card as a "Last
        backfill: 2026-06-07 14:22 UTC" line so operators can see
        when fresh data last landed without depending on the Redis
        status badge (which expires after 24h).

        UPDATE-only by design — the integration row is created by
        the connect flow, never by this helper.  If no row exists
        (deletion race with backfill completion, or the connect flow
        never ran), the UPDATE silently affects 0 rows and the
        stamp never lands.  Logged at WARNING when that happens so
        operators can correlate a "Last backfill: never" report
        against a missing row instead of guessing.
        """
        now = _now_iso()
        cur = await self._db.execute(
            """
            UPDATE account_integrations
               SET last_backfill_at = ?,
                   updated_at       = ?
             WHERE account_id = ? AND provider_id = ?
            """,
            (now, now, account_id, provider_id),
        )
        # asyncpg returns rowcount; aiosqlite shims it.  Defensive
        # getattr so neither breaks the call.
        rowcount = getattr(cur, "rowcount", None)
        if rowcount is not None and rowcount == 0:
            logger.warning(
                "record_integration_backfill_completion: 0 rows updated "
                "acct=%d provider=%s — no integration row exists, "
                "last_backfill_at stamp lost",
                account_id, provider_id,
            )
        await self._db.commit()

    async def record_integration_health_check(
        self,
        account_id: int,
        provider_id: str,
        *,
        ok: bool,
        message: str = "",
    ) -> None:
        """Update ``last_health_at`` + ``last_health_error`` after a
        ``test_connection`` call (manual or from the periodic health
        check job).  Status auto-flips between ``connected`` and
        ``error`` to keep the dashboard badge accurate without a
        separate write.
        """
        now = _now_iso()
        new_status = "connected" if ok else "error"
        await self._db.execute(
            """
            UPDATE account_integrations
               SET status            = ?,
                   last_health_at    = ?,
                   last_health_error = ?,
                   updated_at        = ?
             WHERE account_id = ? AND provider_id = ?
            """,
            (
                new_status, now,
                "" if ok else (message[:500] if message else "unknown error"),
                now, account_id, provider_id,
            ),
        )
        await self._db.commit()

    async def delete_account_integration(
        self, account_id: int, provider_id: str,
    ) -> bool:
        """Hard-delete the integration row.  Returns True if a row was
        removed.

        Companies + Samsara API keys live in the separate ``companies``
        table and are NOT touched here — disconnecting the integration
        pauses ingest but leaves the credentials available for
        reconnect.  The dashboard's disconnect flow asks for explicit
        confirmation if the owner also wants to wipe credentials.
        """
        cur = await self._db.execute(
            "DELETE FROM account_integrations "
            "WHERE account_id = ? AND provider_id = ?",
            (account_id, provider_id),
        )
        await self._db.commit()
        return (getattr(cur, "rowcount", 0) or 0) > 0

    # ── Account-fan-out helpers (used by the scheduler) ──────────

    async def list_accounts_with_enabled_capability(
        self, capability: str,
    ) -> list[tuple[int, str]]:
        """Account/provider pairs whose feature_toggles enable the
        given capability.

        Used by the scheduler to fan a job out only to accounts that
        actually want it.  Caller iterates the result and calls the
        provider client for each row.  Returns ``[(account_id,
        provider_id), ...]`` sorted by account_id for deterministic
        execution order.
        """
        cur = await self._db.execute(
            """
            SELECT account_id, provider_id, feature_toggles
              FROM account_integrations
             WHERE status = 'connected'
             ORDER BY account_id, provider_id
            """,
        )
        out: list[tuple[int, str]] = []
        for row in await cur.fetchall():
            d = dict(zip(
                ("account_id", "provider_id", "feature_toggles"), row,
            ))
            toggles = _safe_json_loads(d.get("feature_toggles") or "", {})
            cap_cfg = toggles.get(capability) or {}
            if cap_cfg.get("enabled"):
                out.append((int(d["account_id"]), str(d["provider_id"])))
        return out

    # ── Internal: row → object ────────────────────────────────────

    def _row_to_integration(self, row: Any) -> AccountIntegration:
        """Convert a raw DB row into an ``AccountIntegration``.

        Handles credential decryption with a soft failure mode — if
        the decrypt raises (key rotation drift, corrupted ciphertext)
        we return ``credentials={}`` and log so the caller can surface
        a "reconnect needed" prompt in the dashboard.
        """
        from infra.crypto import decrypt as _dec

        d = dict(zip(_INTEGRATION_COLUMNS, row))
        encrypted = d.get("credentials_enc") or ""
        creds: dict[str, Any] = {}
        if encrypted:
            try:
                creds_raw = _dec(encrypted)
                creds = json.loads(creds_raw) if creds_raw else {}
                if not isinstance(creds, dict):
                    logger.warning(
                        "account_integrations row acct=%s provider=%s: "
                        "decoded credentials not a dict (got %s)",
                        d.get("account_id"), d.get("provider_id"),
                        type(creds).__name__,
                    )
                    creds = {}
            except Exception:
                logger.exception(
                    "account_integrations row acct=%s provider=%s: "
                    "credential decryption failed — owner will need to reconnect",
                    d.get("account_id"), d.get("provider_id"),
                )
                creds = {}

        return AccountIntegration(
            account_id=int(d.get("account_id") or 0),
            provider_id=str(d.get("provider_id") or ""),
            status=str(d.get("status") or "connected"),
            connected_at=str(d.get("connected_at") or ""),
            credentials=creds,
            feature_toggles=_safe_json_loads(d.get("feature_toggles") or "", {}),
            cadence_overrides=_safe_json_loads(d.get("cadence_overrides") or "", {}),
            last_health_at=str(d.get("last_health_at") or ""),
            last_health_error=str(d.get("last_health_error") or ""),
            last_backfill_at=str(d.get("last_backfill_at") or ""),
            created_by=int(d.get("created_by") or 0),
            created_at=str(d.get("created_at") or ""),
            updated_at=str(d.get("updated_at") or ""),
        )
