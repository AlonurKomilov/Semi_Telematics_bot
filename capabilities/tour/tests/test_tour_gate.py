"""Tours is a service granted per role (can_view_tours, owner decision
2026-09-10): the signals door asks the verb."""
import os, re
os.environ.setdefault("ENCRYPTION_KEY", "")
from tests._repo import REPO

def test_the_signals_door_asks_the_verb():
    src = open(os.path.join(REPO, "capabilities/tour/router.py"), encoding="utf-8").read()
    assert "Depends(get_current_user)" not in src
    assert 'require_permission("can_view_tours")' in src
