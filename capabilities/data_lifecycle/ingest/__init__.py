"""Ingest hub — the ACQUIRE engine of the data-lifecycle family.

Feature contributions are discovered exactly as the sibling hubs do:
module-name strings + ``make_discover`` — capabilities never import
features, and the AST layer guard keeps it that way.
"""

from __future__ import annotations

from .._common import make_discover
from .registry import (  # noqa: F401
    IngestDataset,
    all_datasets,
    get_dataset,
    register_dataset,
)

# Modules that declare ingest datasets.  Each owns what it acquires.
_CONTRIBUTORS = (
    "features.vehicles.lifecycle",   # vehicles.state (first; siblings follow as sync.py splits)
)

discover = make_discover(_CONTRIBUTORS)
