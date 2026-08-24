"""The wire shape one callout takes.

Deliberately small: a stable ``key`` plus the facts a renderer cannot
derive.  The WORDS live in the dashboard's locale files and the
structure (kind, severity) in ``calloutCatalog.ts`` — so re-wording a
callout, or translating it into a ninth language, never touches the
backend or the API contract.

``placement`` is NOT here on purpose.  The same callout renders as a
page strip on the vehicle page, an inline note beside the Fuel field,
and a chip in the vehicle list; where it goes is the call site's
decision.  Putting it on the wire would make the server responsible
for layout and break the first time two surfaces disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Callout:
    """One statement about one thing.

    ``entity`` addresses what the callout is about — ``""`` means the
    surface as a whole, ``"vehicle:<telematics id>"`` one record.  The
    prefix is part of the value so a page holding two kinds of row can
    key them in one map without a second field.
    """

    key: str
    entity: str = ""
    since: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def callout_wire(callouts: list[Callout]) -> list[dict]:
    """Serialize for the API.

    Empty ``since``/``params`` are dropped rather than sent as null —
    a 500-row vehicle list should not carry the skeleton of a callout
    it does not have.
    """
    out: list[dict] = []
    for c in callouts:
        item: dict[str, Any] = {"key": c.key}
        if c.entity:
            item["entity"] = c.entity
        if c.since:
            item["since"] = c.since
        if c.params:
            item["params"] = c.params
        out.append(item)
    return out
