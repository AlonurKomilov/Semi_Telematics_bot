"""Contain custom SPA file serving within its public build directory."""

from pathlib import Path

from fastapi import HTTPException


def public_file_path(directory: str, requested_path: str) -> Path:
    """Resolve symlinks and dot segments before checking containment.

    Unsafe paths are 404s, never candidates for the SPA's HTML fallback.
    Build directories must be writable only by the deployment process.
    """
    try:
        relative = Path(requested_path)
        if relative.is_absolute() or "\x00" in requested_path:
            raise ValueError("absolute or invalid path")
        root = Path(directory).resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("outside public directory")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="File not found") from None
    return candidate
