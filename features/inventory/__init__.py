"""Onboard Inventory — what physically lives in each truck.

Dashcam, fuel card, toll transponder, ELD, tablet and the rest, each with
a status lifecycle and an immutable accountability trail: who did what,
and which driver had the truck at that moment.

Named "inventory" (what is INSIDE this truck) — not "equipment", which in
trucking means the tractors and trailers themselves, and not "parts",
which is the stockroom feature next door.

A FEATURE, not a component of Vehicles.  It began as one, and the move
out is deliberate: it has its own page, its own AI import, its own
retention need and its own trail, and an owner should be able to grant
it to somebody who may not administer trucks.  The one thing it still
borrows is the vehicle registry — a unit number resolves to a truck
there — which is a reference, not ownership.

The storage mixin and its two tables keep their ``vehicle_inventory_*``
names, and the retention target keeps ``vehicles.inventory_events``:
those names are written into ``retention_runs`` history and into every
migration that has already run, and renaming them would orphan both for
no gain a customer could see.  The same call the Billing and Driver Pay
renames made about their deepest identifiers.
"""
