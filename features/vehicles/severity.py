"""Deprecated home — the canonical module is
``capabilities/vehicles/severity.py``.

Severity is consumed by the Samsara ingest, the alerting pipeline, the
warehouse reader and the AI tools, so it belongs at capability level:
``capabilities/integrations`` must not import ``features``, and an
ingest that cannot reach the classifier is an ingest that re-derives it.

Kept as a re-export so existing importers keep working; new code should
import from the capability.
"""

from capabilities.vehicles.severity import (  # noqa: F401
    classify_fault_severity,
    classify_is_critical,
    lamps_are_critical,
)
