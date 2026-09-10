"""Spreadsheet-formula neutralisation for CSV we hand to people.

The outbound sibling of :mod:`infra.file_safety`.  That module distrusts
bytes coming IN; this one distrusts the values going OUT, because a CSV
is not only data — Excel, LibreOffice and Sheets read a cell beginning
with ``=``, ``+``, ``-`` or ``@`` as a formula and evaluate it on open.
The classic payload is ``=cmd|'/C calc'!A1``: a DDE call that asks the
spreadsheet to start a process.  Modern Excel prompts first, but the
prompt is the only thing between a fleet manager and someone else's
command, and prompts get clicked.

This is not hypothetical here.  On 2026-09-08 a probe against production
ran a DOT-binder export with ``vehicle==cmd|'/C calc`` — it tested for
exactly this and we had no guard anywhere.

The reach is what makes it a tenant problem rather than a self-inflicted
one.  Vendors and parts carry ``global_vendor_id`` / ``global_part_id``
links into shared directories, and Service Tasks share a canonical
vocabulary, so text one account types can appear in another account's
export.  A malicious tenant plants the formula once; a different
customer's machine runs it.

``'`` (apostrophe) is the fix every spreadsheet understands: it marks the
cell as literal text and is not itself displayed as content.

Numbers are deliberately left alone.  Real exports carry ``-12.5`` miles
and ``-3.2`` MPG, and prefixing those would turn columns of figures into
text that will not sum — a broken report is not a safer one.  So a value
is only escaped when it is a STRING that starts with a dangerous
character AND does not parse as a number.
"""

from __future__ import annotations

import csv
from typing import Any, Iterable

# Leading characters a spreadsheet may treat as the start of a formula.
# Tab / CR / LF are included because some importers strip them and then
# read whatever follows as the first character.
FORMULA_LEAD: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r", "\n")


def _is_number(text: str) -> bool:
    """True when the whole string is a plain number (``-12.5``, ``+3``)."""
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def csv_safe(value: Any) -> Any:
    """Return ``value`` with any formula lead neutralised.

    Non-strings (including ``int``/``float``/``bool``/``None``) pass
    through untouched — ``csv`` renders them itself and none of them can
    carry a lead character we did not put there.
    """
    if not isinstance(value, str):
        return value
    if not value or value[0] not in FORMULA_LEAD:
        return value
    if _is_number(value):
        return value
    return "'" + value


class SafeWriter:
    """A ``csv.writer`` that runs every cell through :func:`csv_safe`.

    Wrapping the writer rather than the call sites is the point: this
    module's first job covered ninety ``writerow`` calls across eight
    generators and three routers, and a guard applied per call site is a
    guard someone forgets on the ninety-first.  Anything else on the
    writer (``dialect``, ``writerows``) still works — ``__getattr__``
    forwards it.
    """

    def __init__(self, writer: Any) -> None:
        self._writer = writer

    def writerow(self, row: Iterable[Any]) -> Any:
        return self._writer.writerow([csv_safe(cell) for cell in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


def safe_writer(fileobj: Any, *args: Any, **kwargs: Any) -> SafeWriter:
    """Drop-in for ``csv.writer`` — same signature, escaped cells."""
    return SafeWriter(csv.writer(fileobj, *args, **kwargs))
