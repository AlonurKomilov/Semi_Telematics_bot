"""Driver feature — the data contract.

Hubs and tools read driver data through here, never via
``samsara_client`` directly (service-contract rule, docs/FEATURES.md).
"""

from __future__ import annotations

from infra.services import get_client
from features.vehicles.service import prepare_companies


async def get_drivers(account_id: int) -> list[dict]:
    """All fleet drivers (merged across the account's companies)."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_drivers()
