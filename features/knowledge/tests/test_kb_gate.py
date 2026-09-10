"""Knowledge Base is a Shared feature granted per role (can_view_knowledge_base,
owner decision 2026-09-10): every door of its API asks the verb."""
import os, re
os.environ.setdefault("ENCRYPTION_KEY", "")
from tests._repo import REPO

def test_every_door_asks_the_verb():
    src = open(os.path.join(REPO, "features/knowledge/router.py"), encoding="utf-8").read()
    assert "Depends(get_current_user)" not in src
    assert len(re.findall(r'require_permission\("can_view_knowledge_base"\)', src)) >= 16
