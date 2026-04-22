"""vehicle_catalog capability — fleet vehicle data facade."""

from capabilities.vehicle_catalog.service import (  # noqa: F401
    prepare_companies,
    get_company_codes,
    get_fleet_overview,
    get_vehicle_detail,
    get_critical_faults,
)
