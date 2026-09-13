"""Every scheduled job has a name the operator can read.

``_JOB_META`` is what the console's Scheduler page labels a job with —
its section and its one-line description.  A job registered without an
entry there is not broken; it is WORSE than broken, because it runs
every week and appears on that page as a bare id with no section, which
is exactly the shape a reader skips.

Nothing paired the two until now: the catalog is hand-written beside a
hundred lines of `add_job`, and the only thing keeping them together was
whoever last added a job remembering to.  Three had already slipped —
machinery_watchdog, bot_health_daily and warehouse_catalog_comments were
running daily and reaching that page nameless.
"""
from __future__ import annotations

import ast

from tests._repo import REPO

SCHEDULER = REPO / "interfaces" / "bot" / "scheduler.py"


def _registered_ids() -> set[str]:
    """Every literal `id=` handed to scheduler.add_job, read rather than
    run — registering for real would need a live Application."""
    tree = ast.parse(SCHEDULER.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_job"):
            continue
        for kw in node.keywords:
            if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str):
                ids.add(kw.value.value)
    return ids


def _catalogued_ids() -> set[str]:
    tree = ast.parse(SCHEDULER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_JOB_META"
                        for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError("_JOB_META is not a dict literal any more")


def test_every_registered_job_is_named_in_the_catalog():
    missing = sorted(_registered_ids() - _catalogued_ids())
    assert not missing, (
        f"these jobs run but the operator console cannot label them: {missing}. "
        f"Add a line to _JOB_META with its section and what it does.")


# THE REVERSE IS NOT TESTED, and the first draft of this file got it
# wrong.  Asserting that every catalogued id is registered failed on
# thirty-three perfectly live jobs — the warehouse rollups, the alert
# checks and the Datatruck syncs all register in loops with computed ids
# (`id=f"datatruck_sync_{resource}"`), which a reader of the source
# cannot resolve.  Absence from the literal set is not evidence of
# absence, and a guard that says otherwise fails for a reason that has
# nothing to do with the thing it is guarding.


def test_the_weekly_poi_import_is_one_of_them():
    """The map's built-in layers are fetched by it, and a silent week is
    a week of the map quietly falling back to asking a mirror per pan."""
    assert "poi_import" in _registered_ids()
    assert "poi_import" in _catalogued_ids()
