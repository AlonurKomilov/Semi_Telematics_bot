"""Where to open a truck at the provider that supplies it.

One builder, three callers: the vehicle detail page, the browser
extension's panel, and the dashboard's live map.  Kept out of the
routers because a second copy is how two surfaces come to disagree
about where a truck lives.

A source appears only when a real PER-VEHICLE url exists for it.
Datatruck publishes none, so it contributes no link rather than a
button that lands on somebody's dashboard root — the truck the operator
clicked must be the truck they get.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def build_provider_links(account_id: int, vehicle) -> list[dict[str, str]]:
    """Links for one registry vehicle.  Never raises: a provider lookup
    that is slow or down costs a BUTTON, never the page around it."""
    links: list[dict[str, str]] = []
    if not getattr(vehicle, "telematics_ref", None):
        return links
    try:
        from adapters.samsara.client import samsara_vehicle_url
        from infra.services import get_client

        client = await get_client(account_id)
        # Idempotent and instance-cached: the first call per process
        # fetches, the rest are free.
        await client.ensure_org_ids()
        url = samsara_vehicle_url(
            client.org_ids.get(vehicle.company_code, ""), vehicle.telematics_ref)
        if url:
            links.append({
                "source": "samsara",
                "label": "Open in Samsara",
                "url": url,
            })
    except Exception:
        logger.debug("samsara link unavailable acct=%d vehicle=%s",
                     account_id, getattr(vehicle, "id", "?"), exc_info=True)
    return links
