"""Content-based file-type validation for untrusted (public) uploads.

The driver-application form is PUBLIC — anyone with a recruiting link can
POST files.  We never trust the client-declared Content-Type (trivially
spoofed); instead we sniff the leading bytes (magic numbers) and accept
ONLY real images / PDFs.  An uploaded ``.jpg`` that is actually a Windows
PE, an ELF binary, an HTML/SVG with script, or a zip is rejected here —
it never reaches the object store, and we never execute or render
uploaded bytes as code regardless.

This is defence-in-depth, not antivirus: it blocks the obvious
"executable disguised as an image" class and keeps the object store to a
known-safe media allowlist.  A real AV scan (ClamAV) can be layered on
top later for known-malware signatures.
"""
from __future__ import annotations

# Canonical MIME the public intake accepts — images + PDF only.
SAFE_DRIVER_DOC_MIME: frozenset[str] = frozenset({
    "image/jpeg", "image/png", "image/webp", "application/pdf",
})


def sniff_mime(raw: bytes) -> str | None:
    """Return the real MIME of ``raw`` by magic bytes, or None if unknown.

    Only the formats we accept are recognised; everything else (PE/ELF/
    Mach-O executables, scripts, SVG, HTML, zip/office, etc.) returns
    None so the caller rejects it.
    """
    if not raw or len(raw) < 12:
        return None
    # JPEG: FF D8 FF
    if raw[:3] == b"\xFF\xD8\xFF":
        return "image/jpeg"
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WEBP: 'RIFF' .... 'WEBP'
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    # PDF: '%PDF'
    if raw[:4] == b"%PDF":
        return "application/pdf"
    return None


def validate_upload(raw: bytes, *, max_bytes: int) -> tuple[bool, str, str]:
    """Validate one uploaded blob for the public intake.

    Returns ``(ok, mime, reason)``.  ``ok`` is False with a stable
    ``reason`` key when the blob is empty, too large, or not a recognised
    safe media type.  Never raises — the caller maps a False to a 4xx.
    """
    if not raw:
        return False, "", "empty_file"
    if len(raw) > max_bytes:
        return False, "", "file_too_large"
    mime = sniff_mime(raw)
    if mime is None or mime not in SAFE_DRIVER_DOC_MIME:
        return False, "", "unsupported_file_type"
    return True, mime, ""


# ════════════════════════════════════════════════════════════════════
#  ClamAV per-upload virus scan
#  ────────────────────────────────────────────────────────────────────
#  Env-gated companion to ``validate_upload`` / KB magic-byte check.
#  Catches the "real-format-with-malicious-payload" class that header
#  sniffing misses (PDF with embedded JS exploit, JPEG with stegano
#  payload, EICAR test pattern hidden in a valid container, etc.).
#
#  Activation:
#      CLAMAV_HOST=clamd CLAMAV_PORT=3310   → scan every upload
#      CLAMAV_HOST unset / empty            → no-op, returns clean
#
#  Designed so dev + staging without a ``clamd`` daemon behave exactly
#  like today (zero overhead, zero latency).  Flipping it on in prod is
#  a one-env-var change, no code redeploy required.
#
#  Behaviour on infrastructure failure (clamd down, network blip):
#  FAIL-CLOSED — refuses the upload rather than silently storing
#  unscanned bytes when scanning was explicitly requested.  Operator
#  monitoring should alert on the matching log line.
# ════════════════════════════════════════════════════════════════════

import asyncio as _scan_asyncio  # noqa: E402  (block belongs at end of file)
import collections as _scan_collections  # noqa: E402
import logging as _scan_logging  # noqa: E402
import os as _scan_os  # noqa: E402
import socket as _scan_socket  # noqa: E402
import time as _scan_time  # noqa: E402

_scan_logger = _scan_logging.getLogger(__name__)

# ── Tunable resource controls (env-driven, sensible defaults) ──────
# Concurrency cap: at most N parallel scans across the whole API
# process.  Uploads above this number queue on the semaphore rather
# than spawning unbounded clamd connections.  Default 4 is comfortable
# for a single clamd daemon on a modest VM.
_CLAMAV_MAX_CONCURRENT = max(1, int(_scan_os.getenv("CLAMAV_MAX_CONCURRENT", "4")))
# Rate limit: max scans per second system-wide.  Token-bucket-ish via
# a deque of recent timestamps.  Default 10/s = ~600/min, which is
# above any realistic legitimate burst but blocks a flood-attack.
_CLAMAV_RATE_PER_SEC = max(1, int(_scan_os.getenv("CLAMAV_RATE_PER_SEC", "10")))
# Per-scan timeout in seconds.  Tradeoff: too short and big uploads
# fail spuriously; too long and a stuck clamd wedges the rate limiter.
_CLAMAV_TIMEOUT_SEC = max(1, int(_scan_os.getenv("CLAMAV_TIMEOUT_SEC", "10")))
# Content-type filter: empty (default) → scan every type the upload
# whitelist accepts.  Set to e.g. "application/pdf" to scan ONLY PDFs
# (the highest-risk format) and skip images.
_CLAMAV_SCAN_TYPES = {
    s.strip() for s in (_scan_os.getenv("CLAMAV_SCAN_TYPES") or "").split(",")
    if s.strip()
}

# Module-level semaphore + rate-limiter deque shared across requests.
# Created lazily on first scan so import-time has no side effects.
_av_concurrency_sem: _scan_asyncio.Semaphore | None = None
_av_rate_deque: _scan_collections.deque = _scan_collections.deque()
_av_rate_lock = _scan_asyncio.Lock()


def _clamav_endpoint() -> tuple[str, int] | None:
    """Resolve the configured clamd endpoint, or None when not configured.

    Single read-and-validate so the upload hot path doesn't repeat the
    env lookup + port parsing on every request.
    """
    host = (_scan_os.getenv("CLAMAV_HOST") or "").strip()
    if not host:
        return None
    try:
        port = int(_scan_os.getenv("CLAMAV_PORT", "3310"))
    except ValueError:
        _scan_logger.warning(
            "CLAMAV_PORT is not a valid integer — defaulting to 3310",
        )
        port = 3310
    return (host, port)


def is_av_enabled() -> bool:
    """True when the env is configured to actually scan uploads.

    Callers can use this to decide whether to surface scanning state
    in audit logs / UI — but the scan_or_reject() entrypoint itself is
    always safe to call (no-ops when disabled).
    """
    return _clamav_endpoint() is not None


def av_health() -> dict:
    """Return the scanner's configured state for the operator console.

    Pure read, no I/O.  Surfaces every tunable knob in one shape so
    the System Dashboard's File Scans page can render the current
    policy at a glance without re-reading env vars itself.
    """
    endpoint = _clamav_endpoint()
    return {
        "enabled":          endpoint is not None,
        "host":             endpoint[0] if endpoint else None,
        "port":             endpoint[1] if endpoint else None,
        "max_concurrent":   _CLAMAV_MAX_CONCURRENT,
        "rate_per_sec":     _CLAMAV_RATE_PER_SEC,
        "timeout_sec":      _CLAMAV_TIMEOUT_SEC,
        "scan_types":       sorted(_CLAMAV_SCAN_TYPES) or "all",
    }


async def _wait_for_rate_slot() -> None:
    """Token-bucket-ish gate: ensures the global scan rate stays under
    ``_CLAMAV_RATE_PER_SEC``.  Sleeps the minimum required time when the
    bucket is full instead of returning an error — uploads should never
    be rejected JUST because of the rate limit (that's what the per-
    user upload rate limiter in the KB endpoint is for; this here is
    system-level smoothing to protect clamd)."""
    async with _av_rate_lock:
        now = _scan_time.monotonic()
        # Evict timestamps older than 1s
        while _av_rate_deque and _av_rate_deque[0] < now - 1.0:
            _av_rate_deque.popleft()
        if len(_av_rate_deque) >= _CLAMAV_RATE_PER_SEC:
            # Wait until the oldest in-window slot ages out.
            wait = max(0.0, _av_rate_deque[0] + 1.0 - now)
            if wait > 0:
                await _scan_asyncio.sleep(wait)
                now = _scan_time.monotonic()
                while _av_rate_deque and _av_rate_deque[0] < now - 1.0:
                    _av_rate_deque.popleft()
        _av_rate_deque.append(now)


def _clamd_instream(raw: bytes, host: str, port: int, timeout: float | None = None) -> str:
    """Send ``raw`` to clamd via the INSTREAM protocol and return the verdict.

    clamd INSTREAM wire format:
        send: zINSTREAM\\0
              <4-byte BE chunk length><chunk bytes>...
              <4-byte BE zero terminator>
        recv: "stream: OK\\0"                    → clean
              "stream: <SIGNATURE> FOUND\\0"     → infected
              "stream: <error>\\0"               → scan failure

    Uses a fresh socket per call so a stuck clamd doesn't pile up
    half-open connections in the API process.  Timeout is intentionally
    short — a 10s scan miss is better than wedging an upload request.
    """
    import struct
    # Cap individual chunks at clamd's StreamMaxLength-friendly size.
    # Default clamd StreamMaxLength is 25M which matches our upload cap;
    # 64K chunks keep memory steady on the wire side.
    chunk_size = 64 * 1024
    # Resolve timeout against the module-level configurable default
    # so callers don't need to pass it; explicit ``timeout=`` still wins.
    tmo = timeout if timeout is not None else float(_CLAMAV_TIMEOUT_SEC)
    with _scan_socket.create_connection((host, port), timeout=tmo) as sock:
        sock.sendall(b"zINSTREAM\0")
        # Stream the file body in length-prefixed chunks.
        for i in range(0, len(raw), chunk_size):
            chunk = raw[i:i + chunk_size]
            sock.sendall(struct.pack(">I", len(chunk)) + chunk)
        # Zero-length terminator tells clamd "end of stream, scan it".
        sock.sendall(struct.pack(">I", 0))
        # Verdict is short — single recv is enough.
        sock.settimeout(tmo)
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
            if reply.endswith(b"\0"):
                break
    return reply.rstrip(b"\0").decode("utf-8", errors="replace").strip()


async def scan_or_reject(
    raw: bytes,
    *,
    user_id: int | None = None,
    account_id: int | None = None,
    content_type: str | None = None,
    surface: str = "kb",
) -> tuple[bool, str]:
    """Per-upload virus scan.  Returns ``(clean, reason)``.

    ``clean=True``  → store the file
    ``clean=False`` → reject with the human-readable ``reason`` (caller
                      maps to a 422 / "File rejected: <reason>")

    No-op + always-clean when ``CLAMAV_HOST`` is unset — lets the same
    code path ship to dev/staging without infrastructure.  When scanning
    IS configured but the daemon fails (network, timeout, etc.) this
    function FAILS CLOSED: returns ``(False, "av_scan_unavailable")``
    rather than letting an unscanned file slip through.

    Three lightweight resource controls (env-tunable, see module-top):
      * ``CLAMAV_MAX_CONCURRENT`` — semaphore caps parallel scans
      * ``CLAMAV_RATE_PER_SEC``   — token-bucket smooths bursts
      * ``CLAMAV_SCAN_TYPES``     — optional content-type filter
                                    (e.g. "application/pdf" to skip images)

    Every scan attempt is logged to ``platform.scan_log`` (clean +
    infected + unavailable) so the System Dashboard's File Scans page
    can show count/latency/signatures + daemon outage windows.

    Runs the blocking socket I/O in a thread so a slow clamd doesn't
    block the asyncio event loop's other requests.
    """
    endpoint = _clamav_endpoint()
    if endpoint is None:
        return True, ""

    # Optional content-type filter — skip scanning whatever the
    # operator deemed low-risk (e.g. images when only PDF JS exploits
    # are a concern).  Empty set (default) scans everything.
    if _CLAMAV_SCAN_TYPES and content_type and content_type not in _CLAMAV_SCAN_TYPES:
        return True, ""

    host, port = endpoint
    # Lazy-init the semaphore so module import has no event-loop reqs.
    global _av_concurrency_sem
    if _av_concurrency_sem is None:
        _av_concurrency_sem = _scan_asyncio.Semaphore(_CLAMAV_MAX_CONCURRENT)

    # Rate limit gate (system-wide smoothing) BEFORE the semaphore
    # so queueing happens at the rate gate, not by holding a scan slot.
    await _wait_for_rate_slot()

    verdict_str = "unavailable"
    signature: str | None = None
    latency_ms: int | None = None
    t0 = _scan_time.monotonic()
    try:
        async with _av_concurrency_sem:
            try:
                reply = await _scan_asyncio.to_thread(
                    _clamd_instream, raw, host, port,
                )
            except (OSError, _scan_socket.timeout) as e:
                _scan_logger.error(
                    "ClamAV scan failed (user=%s): %s — failing closed",
                    user_id, e,
                )
                return False, "av_scan_unavailable"
            latency_ms = int((_scan_time.monotonic() - t0) * 1000)
            if reply.endswith("OK"):
                verdict_str = "clean"
                return True, ""
            if "FOUND" in reply:
                # "stream: <SIGNATURE> FOUND" — extract the signature.
                signature = reply.split(":", 1)[-1].rsplit(" FOUND", 1)[0].strip()
                _scan_logger.warning(
                    "ClamAV blocked infected upload (user=%s, signature=%s)",
                    user_id, signature,
                )
                verdict_str = "infected"
                return False, f"infected ({signature})"
            _scan_logger.error(
                "ClamAV returned unexpected verdict (user=%s): %r — failing closed",
                user_id, reply,
            )
            return False, "av_scan_unavailable"
    finally:
        # Best-effort scan_log write — never let logging failure mask
        # the verdict.  Imported lazily so this module stays import-safe
        # at package load.
        try:
            from infra.platform import get_platform_db
            pdb = get_platform_db()
            if pdb is not None:
                await pdb.record_scan_event(
                    account_id=account_id,
                    user_id=user_id,
                    surface=surface,
                    file_size=len(raw),
                    content_type=content_type,
                    verdict=verdict_str,
                    signature=signature,
                    latency_ms=latency_ms,
                )
        except Exception:
            _scan_logger.debug(
                "scan_log record failed — swallowed (verdict=%s)", verdict_str,
            )
