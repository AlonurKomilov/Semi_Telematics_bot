"""Object Store — WHERE THE FILES LIVE (not the database).

Attachments, invoices, photos, driver documents: bytes addressed by
key, behind a backend the tenant chooses — the platform's local disk by
default, or their own Google Drive via OAuth.  The adapter that talks to
those backends is ``adapters/storage/object_store.py``.

  ⚠ Do not confuse with ``adapters/storage/`` — that is the DATABASE
  adapter (SQL mixins on ``Database``).  Both once answered to
  "storage"; this package took the precise word so an import line says
  which one it means.

FROZEN, and deliberately NOT renamed with the package: the persisted
settings keys (``storage.backend``, ``storage.gdrive.*`` — live rows in
``account_settings``; renaming them would silently revert every tenant
to disk), the ``object_store_sync_queue`` table, and the ``/storage/*`` API
routes the dashboard calls.  Code names are ours to fix; wire and data
contracts are not.
"""

