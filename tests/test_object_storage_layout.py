"""Layout guard — the tenant file tree is CI-enforced, not remembered.

Every tenant file belongs under ``account-{id}/{COMPANY}/…``.  The same
tree is mirrored into the customer's own Google Drive, where they browse
it by hand and read each top-level folder as one of their businesses —
so a stray folder there is not a cosmetic problem, it is a fake company
in their filing cabinet.

The rules and the reasoning: capabilities/object_storage/LAYOUT.md.

These tests exist because the rules were broken by code that looked
reasonable in isolation:

  * ``f"applications/{reference}"`` as the no-company fallback put 939
    files at the account root.
  * ``"unnamed-company"`` as the placeholder name read like a real
    business and collected 749 camera photos over a year.
  * ``snap.get("_org", "?")`` fed a display placeholder into a path,
    one edge case away from a directory literally named ``?``.

None of those would have survived a guard, which is why there is one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Where feature code lives.  Scripts are excluded on purpose: the repair
# tooling legitimately names the legacy layouts it exists to clean up.
SOURCE_DIRS = ("features", "capabilities", "adapters", "interfaces", "infra")

# The canonical holding pen.  Any OTHER placeholder name means a second
# pen, which is worse than one badly-named pen: files scatter across
# both and neither looks authoritative.
CANONICAL_PEN = "_generic"

# Placeholder names that have been used before and must not come back.
# "unnamed-company" is the one that caused the incident; the others are
# the shapes a future author would plausibly reach for.
BANNED_PLACEHOLDERS = (
    "unnamed-company", "unnamed_company", "no-company", "nocompany",
    "unknown-company", "misc-company", "default-company",
)


def _source_files() -> list[Path]:
    out: list[Path] = []
    for d in SOURCE_DIRS:
        base = ROOT / d
        if base.is_dir():
            out += [p for p in base.rglob("*.py")
                    if "node_modules" not in p.parts]
    return out


class TestOnlyOneHoldingPen:
    def test_the_pen_is_declared_once_and_starts_with_underscore(self):
        """A name without the underscore reads like a business."""
        from features.work_orders.storage import GENERIC_COMPANY_FOLDER
        assert GENERIC_COMPANY_FOLDER == CANONICAL_PEN
        assert GENERIC_COMPANY_FOLDER.startswith("_"), (
            "the pen must not be mistakable for a company name — that is "
            "exactly how 'unnamed-company' collected a year of photos"
        )

    def test_no_module_invents_its_own_placeholder(self):
        """One pen, named in one place, imported everywhere.

        Only CODE counts.  A comment naming the old placeholder is how
        the next reader learns why the current one is named the way it
        is, and a guard that forbids explaining history would delete the
        reason along with the mistake.
        """
        offenders: list[str] = []
        for path in _source_files():
            for n, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore")
                    .splitlines(), 1):
                code = line.split("#", 1)[0]
                for banned in BANNED_PLACEHOLDERS:
                    if banned in code:
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{n}: {banned!r}")
        assert not offenders, (
            "a second placeholder folder was introduced — import "
            "GENERIC_COMPANY_FOLDER from features.work_orders.storage "
            "instead:\n  " + "\n  ".join(offenders)
        )


class TestTheTwoUnderscoreFoldersStaySeparate:
    """``_generic`` is a bug report; ``_account`` is a home.

    Collapsing them would make the bug report unreadable — real
    account-level data would drown the writers that genuinely failed to
    resolve a company.
    """

    def test_they_are_distinct_and_neither_looks_like_a_business(self):
        from features.work_orders.storage import (
            ACCOUNT_LEVEL_FOLDER, GENERIC_COMPANY_FOLDER,
        )
        assert ACCOUNT_LEVEL_FOLDER != GENERIC_COMPANY_FOLDER
        for name in (ACCOUNT_LEVEL_FOLDER, GENERIC_COMPANY_FOLDER):
            assert name.startswith("_"), (
                f"{name!r} would read as one of the customer's companies"
            )


class TestNothingWritesToTheAccountRoot:
    """A bucket must begin with a company folder, never a bare segment.

    Matches the literal shape the offending line had: a storage bucket
    f-string whose FIRST component is a fixed word rather than a
    resolved company folder.
    """

    # f"applications/{ref}"  /  f"camera-images/{x}"  — a bucket-looking
    # literal that opens with a known top-level segment instead of a
    # company.  Deliberately narrow: it names the segments that are
    # supposed to live INSIDE a company folder.
    NESTED_SEGMENTS = (
        "applications", "camera-images", "parking-maps", "work-orders",
        "inspections", "branding", "drivers",
    )

    # Reading an old path is not writing a new one.  A rows-written-
    # before-the-migration fallback must still be able to name the shape
    # those rows have, or we cannot serve files we already stored.
    # Anything in a "legacy" function is a READ path by that definition;
    # a new WRITE must not hide behind the word.
    LEGACY = re.compile(r"def\s+\w*legacy\w*\s*\(", re.I)

    # THE GAP THAT LET TWO THROUGH.  The rule below only saw f-strings,
    # so ``store.put("knowledge", …)`` and ``folder = "template_refs"``
    # — plain literals — were invisible, and both would have created a
    # folder at the account root.  This catches ANY bucket that is a
    # bare literal with no separator: a valid bucket always names at
    # least a company (or _generic / _account) and then a segment.
    ALLOWED_BARE = {
        # platform-scoped stores have no account prefix, so their bucket
        # is not inside a tenant tree at all
        "avatars", "system", "cache",
    }

    def test_no_bare_string_bucket_on_an_account_store(self):
        call = re.compile(r'\.put\(\s*["\'](?P<b>[A-Za-z0-9_-]+)["\']\s*,')
        assign = re.compile(
            r'^\s*(?:folder|bucket)\s*=\s*["\'](?P<b>[A-Za-z0-9_-]+)["\']\s*$')
        offenders: list[str] = []
        for path in _source_files():
            for n, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore")
                    .splitlines(), 1):
                m = call.search(line) or assign.match(line)
                if m and m.group("b") not in self.ALLOWED_BARE:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        assert not offenders, (
            "a bucket with no company segment lands at the ACCOUNT ROOT. "
            "Use the company folder, GENERIC_COMPANY_FOLDER when it "
            "cannot be resolved, or ACCOUNT_LEVEL_FOLDER when the data "
            "genuinely belongs to the whole account:\n  "
            + "\n  ".join(offenders))

    def test_no_bucket_literal_starts_with_a_nested_segment(self):
        pattern = re.compile(
            r'f"(' + "|".join(self.NESTED_SEGMENTS) + r')/\{')
        offenders: list[str] = []
        for path in _source_files():
            in_legacy = False
            for n, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore")
                    .splitlines(), 1):
                if line and not line[0].isspace() and line.lstrip().startswith("def "):
                    in_legacy = bool(self.LEGACY.search(line))
                if pattern.search(line) and not in_legacy:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        assert not offenders, (
            "a bucket path starts at the ACCOUNT ROOT — it must start "
            "with the company folder, or GENERIC_COMPANY_FOLDER when no "
            "company can be established.  See "
            "capabilities/object_storage/LAYOUT.md:\n  "
            + "\n  ".join(offenders))


class TestTheDisplayPlaceholderNeverReachesAPath:
    """``company`` carries "?" for unknown; ``_org`` carries "".

    ``?`` is a legal filename character.  Feeding the display key into a
    path is one missing org tag away from a directory named ``?``, and
    feeding it into a scope filter lets unattributed rows through.
    """

    # Scoped to modules that actually build storage paths.  Defaulting
    # to "?" is CORRECT in a report, an alert body or a cache key —
    # that is what the placeholder is for.  It is only dangerous where
    # the value can reach a folder name, so that is where we look.
    PATH_BUILDING = ("sanitize_company_folder", "resolve_company_folder",
                     "store.put(", "GENERIC_COMPANY_FOLDER")

    def test_no_org_lookup_defaults_to_the_display_placeholder(self):
        offenders: list[str] = []
        pattern = re.compile(r'''get\(\s*["']_org["']\s*,\s*["']\?["']''')
        for path in _source_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not any(marker in text for marker in self.PATH_BUILDING):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        assert not offenders, (
            'the wire key must default to "" — "?" is a display label '
            "and a legal folder-name character:\n  " + "\n  ".join(offenders))


class TestSanitizerRefusesToEscapeItsFolder:
    @pytest.mark.parametrize("name,expected", [
        (".", CANONICAL_PEN),
        ("..", CANONICAL_PEN),
        ("", CANONICAL_PEN),
        ("   ", CANONICAL_PEN),
        ("a/b", "a_b"),
        ("../../etc", ".._.._etc"),
        ("ACME INC.", "ACME INC."),
    ])
    def test_path_components_and_separators_are_neutralised(
            self, name, expected):
        from features.work_orders.storage import sanitize_company_folder
        assert sanitize_company_folder(name) == expected

    def test_a_company_name_can_never_traverse_upward(self):
        """No input may produce a folder that climbs out of the account."""
        from features.work_orders.storage import sanitize_company_folder
        for hostile in ("../x", "..\\x", "a/../../b", "/etc/passwd",
                        "\x00evil", "./."):
            got = sanitize_company_folder(hostile)
            assert "/" not in got and "\\" not in got
            assert got not in (".", "..")


class TestTestsDoNotWriteIntoTheLiveTree:
    def test_conftest_pins_the_object_store_root(self):
        """accounts.id starts at 10000001, the SAME id as the first real
        account — so an unpinned root writes fixtures into a customer's
        folders.  It did, for months."""
        src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        assert 'os.environ["OBJECT_STORE_ROOT"]' in src, (
            "conftest must ASSIGN OBJECT_STORE_ROOT (not setdefault — an "
            "exported production path would win) before importing "
            "adapters.storage, which freezes infra.config's value"
        )
        assign = src.index('os.environ["OBJECT_STORE_ROOT"]')
        first_storage_import = src.index("from adapters.storage import")
        assert assign < first_storage_import, (
            "the pin must come BEFORE the first adapters.storage import — "
            "infra.config reads the variable once, at module import"
        )

    def test_the_root_is_actually_a_throwaway_during_this_run(self):
        import infra.config as cfg
        assert "userdata" not in cfg.OBJECT_STORE_ROOT or "tmp" in cfg.OBJECT_STORE_ROOT, (
            f"tests are pointed at {cfg.OBJECT_STORE_ROOT!r}, which looks "
            "like the real tenant tree"
        )


def test_the_layout_document_exists():
    """The guard cites it in every failure message."""
    doc = ROOT / "capabilities" / "object_storage" / "LAYOUT.md"
    assert doc.is_file(), "the layout SSOT is missing"
    text = doc.read_text(encoding="utf-8")
    for must in ("_generic", "OBJECT_STORE_ROOT", "sanitize_company_folder"):
        assert must in text, f"the layout doc no longer explains {must}"


class TestTheDataRootSurvivesMovingTheCode:
    """A worktree, a deploy to /srv, a second checkout — the files stay
    reachable.

    Every stored path is relative (``data/userdata/account-N/…``) and
    the resolver builds candidates from the CODE's location, so moving
    the checkout points all ~18,000 of them into a tree that has no
    files.  The app looks healthy and every document 404s.

    Setting an absolute ``OBJECT_STORE_ROOT`` does not fix that by
    itself — it only changes where NEW writes go, and the existing rows
    are still relative.  These pin the rebase that makes the setting
    cover both.
    """

    STORED = "data/userdata/account-1/ACME INC/camera-images/1_forward.jpg"

    def _elsewhere(self, tmp_path):
        """A data root outside any checkout, holding one real file."""
        f = tmp_path / "account-1" / "ACME INC" / "camera-images"
        f.mkdir(parents=True)
        (f / "1_forward.jpg").write_bytes(b"jpeg")
        return str(tmp_path)

    def test_a_moved_checkout_still_finds_an_existing_relative_row(
            self, tmp_path, monkeypatch):
        import infra.config as cfg
        from adapters.storage.object_storage import resolve_disk_path
        monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT",
                            self._elsewhere(tmp_path), raising=False)
        got = resolve_disk_path(self.STORED, project_root="/nonexistent/worktree")
        assert got, (
            "an existing row stopped resolving after the code moved — "
            "this is the 404-everything failure the rebase prevents"
        )
        assert got.endswith("1_forward.jpg")

    def test_the_checkouts_own_data_still_wins(self, tmp_path, monkeypatch):
        """The rebase is a FALLBACK.  A checkout that owns its data must
        keep hitting that first, or a stale configured root would shadow
        the real files."""
        import os
        import infra.config as cfg
        from adapters.storage.object_storage import _disk_path_candidates
        monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT",
                            self._elsewhere(tmp_path), raising=False)
        local_root = tmp_path / "checkout"
        (local_root / "data" / "userdata" / "account-1" / "ACME INC"
         / "camera-images").mkdir(parents=True)
        cands = _disk_path_candidates(self.STORED, str(local_root))
        assert cands[0] == os.path.join(str(local_root), self.STORED), (
            "the project-relative candidate must be tried first"
        )

    def test_a_relative_root_adds_no_rebase(self, monkeypatch):
        """Only an ABSOLUTE root names a location; a relative one is
        just the in-checkout default and must not widen the search."""
        import infra.config as cfg
        from adapters.storage.object_storage import _disk_path_candidates
        monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT", "data/userdata",
                            raising=False)
        cands = _disk_path_candidates(self.STORED, "/some/root")
        assert all(c.startswith("/some/root") for c in cands), cands

    def test_the_rebase_cannot_escape_the_configured_root(
            self, tmp_path, monkeypatch):
        """Containment still applies to the new candidate."""
        import infra.config as cfg
        from adapters.storage.object_storage import _disk_path_candidates
        monkeypatch.setattr(cfg, "OBJECT_STORE_ROOT",
                            self._elsewhere(tmp_path), raising=False)
        for hostile in ("data/userdata/../../../../etc/passwd",
                        "data/../../../etc/shadow"):
            for c in _disk_path_candidates(hostile, "/some/root"):
                assert "etc/passwd" not in c and "etc/shadow" not in c, c
