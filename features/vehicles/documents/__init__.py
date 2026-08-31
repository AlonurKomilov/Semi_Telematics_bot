"""Vehicle Documents — a component of the Vehicle feature.

The paperwork one truck carries: registration, title, insurance,
annual-inspection certificates.  Its own mini-home beside
``inventory/`` because it is the same kind of thing — a self-contained
component of Vehicles with its own storage, its own permissions and
its own lifecycle hooks, not a loose file in the feature root.

Files live in the company's own folder tree,
``{COMPANY}/vehicles/{unit}/`` — mirrored into the customer's Drive and
browsed by hand, which is why the folder is named by unit number.
``capabilities/object_storage/docs/LAYOUT.md`` is the law for that path; the
bucket helpers live in ``paths.py`` beside this file; they used to
sit in ``features/work_orders/storage.py`` beside the
driver and work-order ones, because tenant path composition has ONE
home and splitting it is this repo's recurring incident.

Retiring a truck moves its folder to ``vehicles/_archive/{date}/{unit}/``
and restoring brings it home — ``service.py``.
"""

# The ROUTER is deliberately not re-exported here.  ``inventory/``
# does the same: re-exporting it would make ``documents.router`` mean
# both the APIRouter and the submodule that defines it, and the name
# that resolves depends on import order.  Consumers import the
# submodule (``from features.vehicles.documents import router``) and
# take ``.router`` off it, exactly as app.py does for inventory.

# Archiving a VEHICLE moves the whole truck folder and is the vehicles
# feature's job (``features/vehicles/folder_archive.py``) — it carries
# work orders too, and reaching into this package for it was what made
# a vehicle archive look like a document archive.

__all__ = [
]
