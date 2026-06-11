"""Datatruck-shaped storage tables — TMS data, kept separate from
the existing Samsara/telemetry tables.

Each table is owned by exactly one feature (drivers, trucks, trailers,
orders, work_orders) and lives in its own mixin so dropping Datatruck
later means dropping one subpackage, no schema entanglement with
Samsara.

Schema layout (planned for next phase, NOT yet present in
``platform_schema.py``):

  datatruck_drivers
    id INTEGER PRIMARY KEY
    account_id INTEGER NOT NULL
    external_id TEXT NOT NULL          -- Datatruck's row id
    name, license_no, cdl_class, cdl_expiry, phone, …
    last_seen_at TEXT NOT NULL         -- when sync last saw this row
    UNIQUE(account_id, external_id)

  datatruck_trucks      — similar shape; VIN, plate, make, model, year, …
  datatruck_trailers    — similar shape; VIN, plate, …
  datatruck_orders      — pickup/delivery, rate, status, assigned driver+truck
  datatruck_work_orders — type, parts, labour, status, dates

The mixins themselves are placeholder stubs until the sync layer
lands.  Keeping the package in place now avoids a future "where do
these go?" question and signals the intended architecture.
"""

from __future__ import annotations
