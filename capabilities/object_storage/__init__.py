"""Object Store — WHERE THE FILES LIVE (not the database).

Attachments, invoices, photos, driver documents: bytes addressed by
key, behind a backend the tenant chooses — the platform's local disk by
default, or their own Google Drive via OAuth.  The adapter that talks to
those backends is ``adapters/storage/object_store.py``.

  ⚠ Do not confuse with ``adapters/storage/`` — that is the DATABASE
  adapter (SQL mixins on ``Database``).  Both once answered to
  "storage"; this package took the precise word so an import line says
  which one it means.

**Before writing any code that stores a file, read LAYOUT.md next to
this file.**  It is the law for WHERE a tenant file goes:
``account-{id}/{COMPANY DISPLAY NAME}/…``, with nothing but a company
folder or the ``_generic`` holding pen at the account root.  That tree
is mirrored into the customer's own Drive, where they browse it by hand
and read each top-level folder as one of their businesses — so a stray
folder there is a fake company in their filing cabinet, not a cosmetic
bug.  ``tests/test_object_storage_layout.py`` enforces the rules; the
document explains why each one exists and what broke without it.

FROZEN, and deliberately NOT renamed with the package: the persisted
settings keys (``storage.backend``, ``storage.gdrive.*`` — live rows in
``account_settings``; renaming them would silently revert every tenant
to disk), the ``object_storage_sync_queue`` table, and the ``/storage/*`` API
routes the dashboard calls.  Code names are ours to fix; wire and data
contracts are not.
"""

