"""One-shot backfill: ensure existing on-disk blobs (camera images,
parking maps, user avatars) live under the configured object-store
root.

Why this exists
---------------
Before Phase 5 the codebase wrote blobs to hard-coded paths
(``data/camera_images/...``, ``data/parking_maps/...``,
``data/avatars/...``).  The new ``DiskObjectStorage`` defaults to the
same layout, so for the default deployment **no migration is needed**
— this script is a no-op safety check.

If a deployment changes ``OBJECT_STORE_ROOT`` (or eventually swaps to
S3/GCS), running this script copies the legacy files into the new
backend so DB rows like ``camera_checks.image_path`` keep resolving.

Usage
-----
    python3 -m scripts.backfill_object_storage          # report only
    python3 -m scripts.backfill_object_storage --apply  # actually copy
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Allow running directly: ``python3 scripts/backfill_object_store.py``
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.storage.object_storage import get_object_storage, DiskObjectStorage  # noqa: E402
from infra import config  # noqa: E402

logger = logging.getLogger("backfill")
logging.basicConfig(level=logging.INFO, format="%(message)s")

LEGACY_ROOT = "data"
BUCKETS = ("camera_images", "parking_maps", "avatars")


def _copy_bucket(bucket: str, store, apply: bool) -> tuple[int, int]:
    """Return (copied, skipped) counts for a single bucket."""
    src_dir = os.path.join(LEGACY_ROOT, bucket)
    if not os.path.isdir(src_dir):
        logger.info("[%s] no legacy directory at %s — skipping", bucket, src_dir)
        return (0, 0)

    copied = 0
    skipped = 0
    for fname in os.listdir(src_dir):
        src_path = os.path.join(src_dir, fname)
        if not os.path.isfile(src_path):
            continue
        if store.exists(bucket, fname):
            skipped += 1
            continue
        if not apply:
            logger.info("[%s] would copy %s", bucket, fname)
            copied += 1
            continue
        try:
            with open(src_path, "rb") as f:
                store.put(bucket, fname, f.read())
            copied += 1
        except Exception as e:
            logger.warning("[%s] copy failed for %s: %s", bucket, fname, e)
    return (copied, skipped)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the copy.  Without this flag the script is dry-run.",
    )
    args = parser.parse_args()

    store = get_object_storage()
    backend = config.OBJECT_STORE_BACKEND or "disk"
    logger.info("Backend: %s, root: %s", backend, config.OBJECT_STORE_ROOT)

    # Disk → same root short-circuit: nothing to do.
    if isinstance(store, DiskObjectStorage) and (config.OBJECT_STORE_ROOT or "data") == LEGACY_ROOT:
        logger.info(
            "Disk backend with legacy root '%s' — files are already in place. "
            "Nothing to backfill.", LEGACY_ROOT,
        )
        return 0

    total_copied = 0
    total_skipped = 0
    for bucket in BUCKETS:
        c, s = _copy_bucket(bucket, store, apply=args.apply)
        total_copied += c
        total_skipped += s
        logger.info("[%s] copied=%d skipped=%d", bucket, c, s)

    if not args.apply:
        logger.info("Dry-run complete.  Re-run with --apply to perform the copy.")
    logger.info("Total: copied=%d, skipped=%d", total_copied, total_skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
