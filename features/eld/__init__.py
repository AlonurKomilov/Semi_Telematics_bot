"""ELD — the hours-of-service mirror.

A read-only reflection of what a certified electronic logging device
currently reports.  The ELD is the system of record; this feature
exists so dispatch can ask "who can take this load, and for how long"
without opening the vendor's product, and so the assistant can answer
the same question alongside loads, maintenance and position — which no
single vendor's screen can.

Provider-agnostic by construction: the feature declares the CAPABILITY
it needs (``lifecycle.py``) and the resolver decides which connected
provider serves it.  Nothing here knows the name Samsara.
"""
