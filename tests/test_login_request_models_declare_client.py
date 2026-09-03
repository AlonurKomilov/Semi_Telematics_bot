"""Every login handler that reads ``body.client`` has a model that
declares it.

The browser-extension client taught three handlers to read
``body.client`` and added the field to one request model; the mini
app's and the login widget's models never got it, and every Telegram
sign-in raised AttributeError at the handler's first line — in
production, which serves the edit tree.  Pinned by source: handler
signature → model → field, so the next client that adds a read adds
the field or fails here.
"""

from __future__ import annotations

import re

from tests._repo import REPO

_SRC = (REPO / "interfaces" / "api" / "auth.py").read_text(encoding="utf-8")


def _handlers_reading_body_client() -> dict[str, str]:
    """{handler: model} for every ``async def h(..., body: Model)`` whose
    body mentions ``body.client``."""
    out = {}
    for m in re.finditer(r"^async def (\w+)\([^)]*body: (\w+)\)", _SRC, re.M):
        start = m.end()
        nxt = _SRC.find("\nasync def ", start)
        body = _SRC[start:(len(_SRC) if nxt == -1 else nxt)]
        if "body.client" in body:
            out[m.group(1)] = m.group(2)
    return out


def _model_declares_client(model: str) -> bool:
    m = re.search(rf"class {model}\(BaseModel\):\n((?:    .*\n|\n)*)", _SRC)
    return bool(m) and re.search(r"^    client:", m.group(1), re.M) is not None


def test_every_client_reading_handler_has_the_field():
    readers = _handlers_reading_body_client()
    assert readers, "expected at least the extension-aware login handlers"
    missing = {h: mdl for h, mdl in readers.items() if not _model_declares_client(mdl)}
    assert not missing, (
        f"handlers read body.client on a model without the field: {missing} — "
        "add `client: str | None = None` to the model")
