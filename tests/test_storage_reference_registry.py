"""The orphan scan may only delete files it can fully account for.

``capabilities/object_store/references.py`` declares every DB column that can
hold an object-store reference.  If a column is missing from it, every
file that column points at looks unreferenced — and Phase 2 of the
orphan purge deletes unreferenced files.

That is not hypothetical.  Before the registry existed, discovery was
name-pattern based, and on live data it recognised ZERO of 597 parking
maps, camera images and application documents, marking 437 of 601 files
(95.5 MB) for deletion — FMCSA medical certificates, CDL scans, and the
unsafe-parking evidence covered by a 1095-day retention promise.

So these tests are the safety interlock, not tidiness.  The important
one is ``test_no_undeclared_reference_shaped_columns``: it fails when a
NEW column appears that looks like a file reference and has been neither
registered nor explicitly dismissed.  Adding a column then forces a
decision, which is precisely what the old heuristic never did.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from capabilities.object_store.references import (  # noqa: E402
    NOT_REFERENCES,
    REFERENCES,
    json_leaf_paths,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Words that make a TEXT column look like it might hold a file reference.
# Deliberately WIDER than the old matcher: this test's job is to force a
# human decision on anything suspicious, so over-flagging is the goal.
_SUSPICIOUS = (
    "path", "image", "photo", "file", "avatar", "signature",
    "doc", "media", "attach", "logo", "banner", "object_id", "_url",
)


def _looks_like_reference(column: str) -> bool:
    low = column.lower()
    return any(w in low for w in _SUSPICIOUS)


class TestRegistryIsWellFormed:
    def test_no_duplicate_entries(self):
        seen = [(r.table, r.column) for r in REFERENCES]
        assert len(seen) == len(set(seen)), "a column is registered twice"

    def test_nothing_both_declared_and_dismissed(self):
        declared = {f"{r.table}.{r.column}" for r in REFERENCES}
        overlap = declared & set(NOT_REFERENCES)
        assert not overlap, (
            f"{overlap} are declared as references AND dismissed as "
            f"non-references — the registry contradicts itself"
        )

    def test_every_entry_names_an_owning_feature(self):
        for r in REFERENCES:
            assert r.feature, f"{r.table}.{r.column} has no owning feature"

    def test_dismissals_carry_a_reason(self):
        for key, reason in NOT_REFERENCES.items():
            assert reason.strip(), f"{key} is dismissed with no reason given"


class TestJsonExtraction:
    """``docs_json`` is why the fix could not have been another pattern."""

    def test_extracts_nested_leaf_strings(self):
        blob = '{"cdlFront": "a/b/c.jpg", "nested": {"medical": "d/e/f.pdf"}}'
        assert set(json_leaf_paths(blob)) == {"a/b/c.jpg", "d/e/f.pdf"}

    def test_extracts_from_arrays(self):
        assert set(json_leaf_paths('["x.png", ["y.png"]]')) == {"x.png", "y.png"}

    def test_malformed_json_yields_nothing_rather_than_raising(self):
        assert json_leaf_paths("{not json") == []
        assert json_leaf_paths("") == []

    def test_whole_blob_never_equals_a_path(self):
        """Why whole-value matching silently failed for this column."""
        blob = '{"cdlFront": "a/b/c.jpg"}'
        assert blob != "a/b/c.jpg"
        assert "a/b/c.jpg" in json_leaf_paths(blob)


class TestFeatureCoverage:
    """Every feature that WRITES files must declare where it records them."""

    def test_object_store_writers_have_a_declared_reference(self):
        # Features whose writes are genuinely not recorded in a DB column
        # keyed by path, with the reason they cannot leak an orphan.
        exempt = {
            # avatars are written to a fixed per-user key and overwritten
            # in place; there is no growing set of files to leak.
            "settings",
            # the backfill script is one-shot tooling, not a live writer.
            "scripts",
        }
        declared_features = {r.feature for r in REFERENCES}

        writers: set[str] = set()
        pat = re.compile(r"\bstore\.put\(")
        for root, dirs, files in os.walk(os.path.join(REPO, "features")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                with open(p, encoding="utf-8") as fh:
                    if pat.search(fh.read()):
                        rel = os.path.relpath(p, os.path.join(REPO, "features"))
                        writers.add(rel.split(os.sep)[0])

        missing = writers - declared_features - exempt
        assert not missing, (
            f"features {sorted(missing)} call ObjectStore.put but declare no "
            f"reference column in capabilities/object_store/references.py — every "
            f"file they write would look orphaned to the purge"
        )


@pytest.mark.asyncio
class TestAgainstLiveSchema:
    """Schema-driven checks — these are the ones that catch drift."""

    async def test_declared_columns_exist(self, pg_db):
        cur = await pg_db._db.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        have = {(dict(r)["table_name"], dict(r)["column_name"])
                for r in await cur.fetchall()}
        tables = {t for t, _ in have}

        for r in REFERENCES:
            if r.table not in tables:
                continue  # feature's migration not applied here — fine
            assert (r.table, r.column) in have, (
                f"{r.table}.{r.column} is registered as a file reference but "
                f"that column does not exist — the registry has drifted and "
                f"the scan is silently reading nothing for it"
            )

    async def test_no_undeclared_reference_shaped_columns(self, pg_db):
        """THE interlock: a new suspicious column must be classified."""
        cur = await pg_db._db.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND data_type IN ('text', 'character varying', 'varchar')"
        )
        cols = [dict(r) for r in await cur.fetchall()]

        declared = {f"{r.table}.{r.column}" for r in REFERENCES}
        undeclared = [
            f"{c['table_name']}.{c['column_name']}"
            for c in cols
            if _looks_like_reference(c["column_name"])
            and f"{c['table_name']}.{c['column_name']}" not in declared
            and f"{c['table_name']}.{c['column_name']}" not in NOT_REFERENCES
        ]

        assert not undeclared, (
            "these columns look like object-store references but are neither "
            "registered in REFERENCES nor dismissed in NOT_REFERENCES:\n  "
            + "\n  ".join(sorted(undeclared))
            + "\n\nAdd each one. If it holds a stored-file path, register it — "
              "otherwise every file it points at becomes a purge candidate. "
              "If it does not, dismiss it with the reason."
        )
