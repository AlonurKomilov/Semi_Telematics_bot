"""Report rows carry the tolerance their freshness cue fires on.

The grid's columns render ROWS and never see the envelope, so the
number has to travel with the value it dates — one per dataset, read
from the registry, never typed into a column definition.
"""

from __future__ import annotations

from capabilities.data_lifecycle.staleness import sla_minutes
from capabilities.reporting.router import _stamp_sla


def test_every_row_gets_its_datasets_declared_number():
    rows = _stamp_sla([{"vehicle_name": "101"}, {"vehicle_name": "102"}], "vehicles.faults")
    assert [r["sla_min"] for r in rows] == [sla_minutes("vehicles.faults")] * 2


def test_the_number_is_the_registrys_not_a_literal():
    """Fuel rows are vehicle-state data and faults are their own feed —
    two different declarations, and the stamp must follow the key."""
    fuel = _stamp_sla([{}], "vehicles.state")[0]["sla_min"]
    faults = _stamp_sla([{}], "vehicles.faults")[0]["sla_min"]
    assert fuel == sla_minutes("vehicles.state") and faults == sla_minutes("vehicles.faults")
    assert fuel != faults


def test_an_unknown_dataset_raises_rather_than_stamping_never_stale():
    import pytest
    with pytest.raises(KeyError):
        _stamp_sla([{}], "vehicles.stat")
