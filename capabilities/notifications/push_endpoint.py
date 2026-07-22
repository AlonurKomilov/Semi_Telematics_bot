"""Push-endpoint validation — the anti-SSRF gate for web push.

A push "endpoint" is a client-supplied URL the server will later POST to
on every alert.  Without validation that is an authenticated SSRF
primitive: any user could register ``https://10.0.0.5/...`` (or the cloud
metadata address) as a "device" and have the server call it from inside
the network.

Policy (advisor-settled): no vendor allowlist — an allowlist silently
breaks browsers we didn't enumerate (unknown Android browsers on cab
tablets), while the actually dangerous case is the INTERNAL address.  So:
https-only, no userinfo, length-capped, and every resolved IP must be
globally routable (``ipaddress.is_global`` — rejects loopback, RFC1918,
link-local/metadata, CGNAT, ULA).  Validated at SUBSCRIBE and re-checked
at SEND time (blunts DNS rebinding).  Distinct hostnames are logged so a
vendor allowlist stays a one-day follow-up if abuse ever shows up.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("bot.notifications")

MAX_ENDPOINT_LEN = 1024
# Per-user device cap — bounds the outbound fan-out one account can cause
# (and the serial send latency per event).
MAX_DEVICES_PER_USER = 10

# NAT64 well-known prefix (RFC 6052): 64:ff9b::/96 embeds an IPv4 in its
# low 32 bits.  ``is_global`` reports such an address as global, so
# without a decode an attacker could smuggle an internal v4 (e.g. the
# 169.254.169.254 metadata address) past the IP gate on a NAT64 network.
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")


def _resolve_host(host: str, port: int) -> list[str]:
    """All A/AAAA answers for the host (patchable seam for tests)."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [i[4][0] for i in infos]


def validate_push_endpoint(endpoint: str) -> str | None:
    """``None`` when the endpoint is acceptable, else an error code.

    Blocking (DNS) — call from a thread in async contexts.
    """
    if not endpoint or len(endpoint) > MAX_ENDPOINT_LEN:
        return "bad_endpoint"
    try:
        u = urlsplit(endpoint)
    except ValueError:
        return "bad_endpoint"
    if u.scheme != "https" or not u.hostname:
        return "bad_endpoint"
    if u.username or u.password:
        return "bad_endpoint"
    try:
        ips = _resolve_host(u.hostname, u.port or 443)
    except OSError:
        return "unresolvable"
    if not ips:
        return "unresolvable"
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return "bad_endpoint"
        if not ip.is_global:
            # Loopback / private / link-local / metadata / CGNAT / ULA —
            # never a real push vendor, always a probe.
            return "private_endpoint"
        # NAT64-embedded internal IPv4 reads as global — decode + re-check
        # the embedded address so it can't smuggle 169.254.169.254 etc.
        if ip.version == 6 and ip in _NAT64_WELL_KNOWN:
            embedded = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
            if not embedded.is_global:
                return "private_endpoint"
    return None
