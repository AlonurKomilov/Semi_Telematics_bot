"""Vehicle health — a component of the Vehicle feature.

Owns every hub contribution for vehicle health (docs/FEATURES.md):

  alert.py   — the health alert source (registers ``health_check``)
  report.py  — the Vehicle Health PDF generator (Reports hub)
  tool.py    — the AI ``get_vehicle_health`` tools (AI hub)
  signal.py  — the health score signal (Scoring hub)

Import the submodule you need directly — this ``__init__`` deliberately
re-exports NOTHING so importing one contribution (e.g. the alert source
from the bot) never drags another hub's machinery (e.g. reporting's
pdf_base) into the import graph.

Data comes from the telemetry warehouse via
``capabilities.telemetry.service.get_vehicle_health`` — the component
contributes *behavior* to the hubs, the platform owns ingestion.
"""
