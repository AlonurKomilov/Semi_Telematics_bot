"""Every scope noun a caller passes is a noun the table knows.

`unit_width` RAISES on a noun it does not recognise — deliberately,
because a silent 'all' would widen and a silent 'assigned' would empty,
and neither is a safe guess.  That makes a stale noun a 500, and the
one place it lands is the worst place to land quietly: the live map's
five-second position poll, whose failures the browser panel swallows
on purpose ("the 30s poll surfaces errors; the fast one stays quiet").

The list keeps working, the counts keep updating, and every marker on
the map stops moving.  That is what the owner saw after
`can_view_location` became `can_view_live_map`: the pair table's key is
derived from the canonical flag, so it moved with the rename, and one
call site still asked for `"location"`.

Nothing caught it — not the 340-test move suite, not the 203-test
permission suite, because a string argument is invisible to both.  This
reads the call sites.
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("JWT_SECRET", "x" * 64)
os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.roles import PAIRED_UNIT_FEATURES, PERSON_FEATURES
from tests._repo import REPO

#: Nouns a test may pass to prove the raise happens.
_DELIBERATELY_UNKNOWN = {"not_a_feature"}

_CALLERS = ("member_unit_scope(", "unit_width(", "_unit_width(")


def _nouns(src: str) -> list[str]:
    """The noun argument of every scope call in a file.

    A regex over the whole call matched the FIRST string literal, which
    on `unit_width(account_id, role, user, "vehicles")` is whatever
    keyword happens to come first.  The noun is the LAST positional
    string, so the argument list is walked to its own closing paren and
    split at depth zero — enough parsing for a call, no more.
    """
    out: list[str] = []
    for name in _CALLERS:
        at = 0
        while (at := src.find(name, at)) != -1:
            i = at + len(name)
            depth, start = 1, i
            while i < len(src) and depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            args, depth2, piece = [], 0, ""
            for ch in src[start:i - 1]:
                if ch in "([{":
                    depth2 += 1
                elif ch in ")]}":
                    depth2 -= 1
                if ch == "," and depth2 == 0:
                    args.append(piece); piece = ""
                else:
                    piece += ch
            args.append(piece)
            for a in reversed(args):
                a = a.strip()
                m = re.fullmatch(r"""["']([a-z_]+)["']""", a)
                if m:
                    out.append(m.group(1))
                    break
            at = i
    return out

_SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", "data", "backups", "htmlcov"}


def _python_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def test_every_unit_scope_noun_is_in_the_pair_table():
    known = set(PAIRED_UNIT_FEATURES) | _DELIBERATELY_UNKNOWN
    bad: list[str] = []
    for path in _python_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if "member_unit_scope(" not in src and "unit_width(" not in src:
            continue
        for noun in _nouns(src):
            if noun not in known:
                bad.append(f"{os.path.relpath(path, REPO)}: {noun!r}")
    assert not bad, (
        "scope nouns that the pair table does not know — `unit_width` raises "
        f"on these, and the caller becomes a 500: {sorted(set(bad))}\n"
        f"known: {sorted(PAIRED_UNIT_FEATURES)}")


def test_the_pair_table_still_holds_the_live_map():
    """The noun the rename moved.  Named on its own so the failure says
    which feature, not just 'a noun is missing'."""
    assert "live_map" in PAIRED_UNIT_FEATURES
    assert "location" not in PAIRED_UNIT_FEATURES, (
        "the old noun is back — the table's key is derived from the "
        "canonical flag, so this means the flag moved back too")


def test_person_nouns_are_a_separate_vocabulary():
    """A person noun passed to the UNIT resolver raises the same way.
    Kept here so the two tables cannot quietly merge."""
    assert not (set(PERSON_FEATURES) & set(PAIRED_UNIT_FEATURES)), (
        "a noun is in both tables; one resolver will answer for the wrong "
        "question")
