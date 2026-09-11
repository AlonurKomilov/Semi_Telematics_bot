"""Vehicle vocabulary shared across layers.

Fault severity is not one feature's private rule: the Samsara ingest
stamps it, the alerting pipeline routes on it, the warehouse reader
stores it and several AI tools report it.  It lived under
``features/vehicles`` and ``capabilities/integrations`` is forbidden
from importing ``features`` — a boundary the ingest could only respect
by re-deriving the rule, which is the copy-paste the module exists to
prevent.  ``capabilities/alerting/severity.py`` has named this home in
its docstring since it was written.
"""
