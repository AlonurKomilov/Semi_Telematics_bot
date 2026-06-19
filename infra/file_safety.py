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
