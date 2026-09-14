"""Security — what the platform notices about who is doing what to it.

Born from the 2026-09-08 probe: every signal needed to see it was already
in the database, and nothing read them.  This capability is the reader.

Two parts, in order of arrival:

- ``recorder`` — decides which requests are kept and writes them to
  ``security_requests``: every refusal (401/403/429) from anyone, and
  everything from an account of kind ``monitored``.  Observation only —
  nothing here may refuse a request.
- ``retention`` — the ledger's keep-window, declared the way every other
  dataset declares one.

Operator-facing and cross-account by nature, so it sits with the other
platform-tier capabilities and is read only through ``/system/*``.
"""
