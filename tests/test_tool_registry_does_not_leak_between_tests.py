# repo-wide: guards the SUITE's own isolation machinery (conftest's registry
# snapshot/restore), which no single package owns.
"""Guard: a test that registers a tool cannot leave it behind.

The AI tool registry is process-global module state, and four separate
guards read its contents — the retired-vehicle stance map, the
permission-decision pairing, the executor/declaration pairing, and the
gate-to-feature mapping. A test that registers a tool through the real
decorator and never removes it therefore fails a DIFFERENT file's test,
and only when the two land on the same xdist worker, so it reads as
flakiness rather than as the missing teardown it is.

capabilities/ai/tests/test_tool_result_envelope.py registers three
(_boom_tool, _count_tool, _envelope_tool) and does not clean up. The
root conftest's `_isolate_process_caches` fixture now snapshots and
restores the registry around every test, which is where the identical
fix for the notification channel map already lives — so the leak cannot
cross a test boundary in any package, instead of each file being
expected to remember.

These two tests are deliberately ORDER-DEPENDENT: the first leaks, the
second proves the leak did not survive. pytest runs a file's tests in
declaration order, so this pins the fixture rather than the etiquette.
"""

from capabilities.ai.tools.registry import get_all_tool_schemas, register_tool

_LEAKED = "_leak_probe_tool"


def test_a_test_can_register_a_tool_without_cleaning_up():
    @register_tool({
        "name": _LEAKED,
        "description": "registered on purpose, never removed",
        "parameters": {"type": "object", "properties": {}},
    })
    async def _probe(tool_args, samsara_client, account_id=None, db=None):
        return {"ok": True}

    assert _LEAKED in {s["name"] for s in get_all_tool_schemas()}


def test_the_next_test_does_not_inherit_it():
    names = {s["name"] for s in get_all_tool_schemas()}
    assert _LEAKED not in names, (
        "a tool registered by the previous test survived into this one — "
        "the registry snapshot in the root conftest is not restoring, and "
        "every registry-shape guard in the suite is now one unlucky "
        "worker assignment away from failing for the wrong reason"
    )
    # And the real registry is still intact, not emptied by the restore.
    assert len(names) >= 30, f"only {len(names)} tools after the restore"
