"""Legacy pair names may only DECREASE outside their home files.

Stage E of the verb/scope migration: the ``*_all`` / ``*_vehicle`` pairs
are dying.  Until the physical flip, they still exist as fields, so a
blanket "never mention them" guard would be a lie today — but a NEW
site that reads a dying name is a site the flip will break, and a
migrated site that quietly grows one back is the drift this whole arc
exists to stop.

So: a ratchet.  Every tracked source file outside the pairs' home
files (the roles module, the contract, the bridge, the matrix editor
that still edits pairs, migrations, tests) is counted; a count above
its baseline fails; a count BELOW its baseline also fails, with "lower
it" — a ceiling far above the truth stops being a ratchet.  Part 2 of
stage E drives every entry to zero and deletes this file's baseline.
"""

from __future__ import annotations

import io
import json
import re
import subprocess

from capabilities.permissions.roles import PAIRED_UNIT_FEATURES

_NAMES = sorted({n for pair in PAIRED_UNIT_FEATURES.values() for n in pair})
_RX = re.compile(r"\b(" + "|".join(map(re.escape, _NAMES)) + r")\b")

#: files where the names legitimately LIVE until the physical flip
_HOME = re.compile(
    r"(^|/)(tests?/|.*\.test\.|roles\.py|taxonomy\.py|fold\.py|scope\.py|"
    r"migrations\.py|platform_migrations\.py|modules\.py|permRows\.ts|"
    r"verbGrid\.ts|types/index\.ts|featureCatalog\.ts|RoleViewContext\.tsx|"
    r"generateNav\.ts|scripts/|node_modules/)"
)

#: mentions per file at the moment the ratchet was set (2026-09-02).
#: Lower an entry when you migrate a site; never raise one — with ONE
#: recorded exception: 2026-09-03, the browser extension's scoped token
#: (interfaces/api/auth.py EXTENSION_SCOPE + connect.ts) arrived
#: speaking the Live Map pair.  That is a WIRE contract with a shipped
#: Chrome client, so it migrates as a pair — server emits legacy AND
#: canonical scope strings, the extension checks the canonical one —
#: in part 2, not by editing another author's day-old code.  Entries
#: added: auth.py 2, connect.ts 1.
BASELINE: dict[str, int] = json.loads('''
{
    "adapters/storage/parking.py": 1,
    "capabilities/alerting/relevance.py": 1,
    "capabilities/reporting/router.py": 2,
    "capabilities/scorecards/service.py": 1,
    "features/cameras/router.py": 2,
    "features/events/router.py": 2,
    "features/inspections/router.py": 6,
    "features/location/router.py": 1,
    "features/maintenance/router.py": 4,
    "features/parking/router.py": 3,
    "features/parking/service.py": 2,
    "features/scorecards/router.py": 4,
    "features/vehicles/router.py": 5,
    "interfaces/api/auth.py": 2,
    "interfaces/bot/callbacks/parking.py": 2,
    "interfaces/bot/geofences.py": 1,
    "interfaces/bot/keyboards.py": 2,
    "interfaces/bot/registration.py": 2,
    "interfaces/browser_extension/src/connect.ts": 1,
    "interfaces/dashboard/src/components/tour/types.ts": 2,
    "interfaces/dashboard/src/config/poiLayers.ts": 1,
    "interfaces/dashboard/src/features/live-map/sections/CompanyColorPartition.tsx": 2,
    "interfaces/dashboard/src/features/live-map/sections/GeofenceBoundariesLayer.tsx": 2,
    "interfaces/dashboard/src/features/live-map/sections/UnsafeParkingMarkers.tsx": 1,
    "interfaces/dashboard/src/features/live-map/sections/UtilisationHeatmap.tsx": 2,
    "interfaces/dashboard/src/features/maintenance/ServiceHistoryModal.tsx": 1,
    "interfaces/dashboard/src/features/maintenance/Tasks.tsx": 3,
    "interfaces/dashboard/src/features/maintenance/tour/bulkAdd.ts": 1,
    "interfaces/dashboard/src/features/overview/sections/OverviewKpiGrid.tsx": 1,
    "interfaces/dashboard/src/features/overview/sections/OverviewStatusGrid.tsx": 1,
    "interfaces/dashboard/src/features/reports/DotBinder.tsx": 1,
    "interfaces/dashboard/src/features/reports/ReportsLayout.tsx": 1,
    "interfaces/dashboard/src/features/vehicles/sections/VehicleFaults.tsx": 1,
    "interfaces/miniapp/src/components/BottomNav.tsx": 1,
    "interfaces/miniapp/src/components/PTIChip.tsx": 1
}
''')


def _counts() -> dict[str, int]:
    files = subprocess.run(
        ["git", "ls-files", "--", "*.py", "*.ts", "*.tsx"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    out: dict[str, int] = {}
    for f in files:
        if _HOME.search(f):
            continue
        try:
            n = len(_RX.findall(io.open(f, encoding="utf-8", errors="ignore").read()))
        except OSError:
            continue
        if n:
            out[f] = n
    return out


def test_no_file_grows_a_legacy_pair_name():
    now = _counts()
    grew = [f"{f}: {n} > baseline {BASELINE.get(f, 0)}"
            for f, n in sorted(now.items()) if n > BASELINE.get(f, 0)]
    assert not grew, (
        "a dying name gained a reader — read it through the verb grammar "
        "(can_view_* / can_manage_*) or the width core "
        "(capabilities/permissions/scope.unit_width) instead:\n" + "\n".join(grew))


def test_the_baseline_is_not_above_the_truth():
    now = _counts()
    stale = [f"{f}: baseline {b}, truth {now.get(f, 0)} — lower it"
             for f, b in sorted(BASELINE.items()) if now.get(f, 0) < b]
    assert not stale, "\n".join(stale)
