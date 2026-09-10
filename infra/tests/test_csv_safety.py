"""A CSV we hand to a person must not be a program.

The guard exists because a probe against production on 2026-09-08 ran a
DOT-binder export with ``vehicle==cmd|'/C calc`` — the DDE payload that
asks a spreadsheet to start a process — and at that moment nothing in
this codebase escaped a formula lead anywhere.

Two things are under test, and the second is the one that makes a
security fix survivable: the payload is neutralised, AND real numbers
are left alone. A guard that turns ``-12.5`` miles into text breaks every
report that sums a column, and a broken report gets the guard removed.
"""

from __future__ import annotations

import csv
import io

import pytest

from infra.csv_safety import FORMULA_LEAD, csv_safe, safe_writer


# ── the payloads ──────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    "=cmd|'/C calc'!A1",          # what the probe actually sent
    "=1+1",
    "=HYPERLINK(\"http://x\",\"click\")",
    "@SUM(1+1)*cmd|'/C calc'!A1",
    "+1+1",
    "-1+1",
    "-Bob",                        # a name, not a number → still escaped
    "\t=cmd|'/C calc'!A1",         # importers that strip the tab first
])
def test_a_formula_lead_is_neutralised(payload):
    out = csv_safe(payload)
    assert out == "'" + payload
    assert out[0] not in FORMULA_LEAD


# ── the half that keeps the reports usable ────────────────────────

@pytest.mark.parametrize("value", ["-12.5", "+3", "-1e5", "-0", "3.2"])
def test_numeric_strings_keep_their_sign(value):
    """Efficiency exports carry negative miles and MPG as strings.

    Prefixing these would turn a column of figures into text that Excel
    will not sum — the report would be wrong, quietly, for everyone.
    """
    assert csv_safe(value) == value


@pytest.mark.parametrize("value", [-12.5, -3, 0, 1.5, True, False, None])
def test_non_strings_pass_through_untouched(value):
    assert csv_safe(value) is value


@pytest.mark.parametrize("value", ["", "ACME", "Bob", "N/A", "12.5"])
def test_ordinary_text_is_unchanged(value):
    assert csv_safe(value) == value


# ── the wrapper is what actually protects the call sites ──────────

def test_safe_writer_escapes_every_cell_in_a_row():
    sio = io.StringIO()
    safe_writer(sio).writerow(["ok", "=cmd|'/C calc'!A1", -12.5, "@SUM(1)"])
    row = next(csv.reader(io.StringIO(sio.getvalue())))
    assert row == ["ok", "'=cmd|'/C calc'!A1", "-12.5", "'@SUM(1)"]


def test_safe_writer_escapes_writerows_too():
    sio = io.StringIO()
    safe_writer(sio).writerows([["=a"], ["+b"], ["fine"]])
    rows = list(csv.reader(io.StringIO(sio.getvalue())))
    assert rows == [["'=a"], ["'+b"], ["fine"]]


def test_safe_writer_still_quotes_commas_and_quotes():
    """Wrapping must not cost RFC 4180 correctness."""
    sio = io.StringIO()
    safe_writer(sio).writerow(['a,b', 'say "hi"', "line\nbreak"])
    assert next(csv.reader(io.StringIO(sio.getvalue()))) == [
        "a,b", 'say "hi"', "line\nbreak"]


def test_safe_writer_forwards_unknown_attributes():
    sio = io.StringIO()
    assert safe_writer(sio).dialect is not None


# ── nothing writes CSV around the guard ───────────────────────────

def test_no_production_code_calls_csv_writer_directly():
    """The guard is the writer, so a raw ``csv.writer`` is a hole.

    Eight generators and three routers wrote ninety rows between them;
    a per-call-site escape is one someone forgets on the ninety-first.
    """
    import re
    from tests._repo import REPO

    offenders = []
    for path in list((REPO / "capabilities").rglob("*.py")) + \
                list((REPO / "features").rglob("*.py")) + \
                list((REPO / "interfaces").rglob("*.py")) + \
                list((REPO / "adapters").rglob("*.py")):
        if "/tests/" in str(path) or "node_modules" in str(path):
            continue
        if re.search(r"\bcsv\.writer\(|\bcsv\.DictWriter\(",
                     path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "these write CSV without the formula guard — use "
        "infra.csv_safety.safe_writer: " + ", ".join(offenders))
