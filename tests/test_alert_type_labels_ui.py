"""Every alert type the API can hand the UI has a human row label.

The preferences matrix renders ``TYPE_LABEL[type] || type`` — so a type
registered on the BACKEND without a label on the FRONTEND silently leaks
its raw key ("maintenance") into a list of proper names ("Engine Faults",
"Unsafe Parking").  That regression shipped once; this pins the direction
it actually breaks: Python adds a type, TypeScript forgets the label.

Parsing the TS file is deliberate — the drift is cross-language, so a
same-language test can't see it (same idea as the layer-boundary guards).
"""

from __future__ import annotations

import re
from pathlib import Path

from capabilities.alerting.relevance import ALERT_TYPE_REQUIRED_PERM

_MATRIX = (Path(__file__).resolve().parent.parent
           / "interfaces/dashboard/src/features/alerts/NotifyMatrix.tsx")


def _ui_labelled_types() -> set[str]:
    src = _MATRIX.read_text()
    # Match with or without `export` — the map is module-private (exporting
    # it only bought a react-refresh lint warning), and this guard reads the
    # file as text so it never needed the export.
    block = re.search(
        r"(?:export\s+)?const TYPE_LABEL:\s*Record<string, string>\s*=\s*\{(.*?)\n\};",
        src, re.DOTALL)
    assert block, "TYPE_LABEL map not found in NotifyMatrix.tsx"
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", block.group(1),
                          re.MULTILINE))


def test_every_alert_type_has_a_ui_label():
    labelled = _ui_labelled_types()
    missing = sorted(set(ALERT_TYPE_REQUIRED_PERM) - labelled)
    assert not missing, (
        "These alert types would render their RAW key in the preferences "
        f"matrix — add them to TYPE_LABEL in NotifyMatrix.tsx: {missing}"
    )


def test_no_stale_ui_labels():
    """A label for a type the backend no longer serves is dead weight —
    catch it while the rename is still fresh in someone's head."""
    stale = sorted(_ui_labelled_types() - set(ALERT_TYPE_REQUIRED_PERM))
    assert not stale, (
        f"TYPE_LABEL has entries the backend never sends: {stale}")
