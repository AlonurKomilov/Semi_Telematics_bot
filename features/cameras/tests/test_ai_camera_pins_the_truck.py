"""Guard: the camera frame belongs to the truck that was asked about.

Two defects, one fix.

The company half of the filter was DEAD CODE. get_dashcam_snapshots
merges the raw client rows without stamping a company, so row_company()
returned "" for every snapshot and `"" == co` was False for any vehicle
whose registry row HAS a company code. The tool answered "No recent
camera image found" for those trucks whether or not a frame existed —
broken closed, on most accounts.

With the company half gone, matching by NAME alone is a coin toss
between same-numbered trucks in two companies. The registry row carries
the provider id; that names one record.
"""

import pytest

from features.cameras.ai_tool import check_vehicle_camera


class _V:
    def __init__(self, vid, ref, unit, company):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def list_vehicles(self, account_id, **kw):
        return list(self._rows)


OSY = _V(42, "sam_42", "103", "OSY")
G1 = _V(99, "sam_99", "103", "G1")


def _snap(vehicle_id, name="103", img=b"jpeg"):
    return {"vehicle_id": vehicle_id, "vehicle_name": name,
            "image_bytes": img, "camera_type": "dashcam",
            "event_time": "2026-09-01T00:00:00Z"}


@pytest.fixture
def _stub(monkeypatch):
    import features.cameras.ai_tool as mod
    seen = {}

    async def _analyze(image_bytes, vehicle_name=None, account_id=None):
        seen["bytes"] = image_bytes
        return {"obstructed": False}

    monkeypatch.setattr(mod, "analyze_camera_image", _analyze, raising=False)
    return seen


@pytest.mark.asyncio
async def test_a_truck_with_a_company_code_still_gets_its_frame(monkeypatch, _stub):
    """The original bug: any vehicle whose registry row has a company
    was told there was no image."""
    import features.cameras.service as svc

    async def _snaps(account_id, days=3, *, vehicle_ids=None, company=None):
        return [_snap("sam_42")]

    monkeypatch.setattr(svc, "get_dashcam_snapshots", _snaps, raising=False)
    res = await check_vehicle_camera(
        {"vehicle_name": "103", "company": "OSY"}, None,
        account_id=1, db=_DB([OSY, G1]))

    assert "result" not in res, res
    assert res.get("camera_type") == "dashcam"


@pytest.mark.asyncio
async def test_the_twins_frame_is_not_returned(monkeypatch, _stub):
    import features.cameras.service as svc

    async def _snaps(account_id, days=3, *, vehicle_ids=None, company=None):
        return [_snap("sam_99")]          # only the OTHER company's truck

    monkeypatch.setattr(svc, "get_dashcam_snapshots", _snaps, raising=False)
    res = await check_vehicle_camera(
        {"vehicle_name": "103", "company": "OSY"}, None,
        account_id=1, db=_DB([OSY, G1]))

    assert res.get("result"), res
    assert "No recent camera image" in res["result"]


@pytest.mark.asyncio
async def test_a_scoped_caller_cannot_reach_the_twins_camera(monkeypatch, _stub):
    import features.cameras.service as svc

    async def _snaps(account_id, days=3, *, vehicle_ids=None, company=None):
        return [_snap("sam_42"), _snap("sam_99")]

    monkeypatch.setattr(svc, "get_dashcam_snapshots", _snaps, raising=False)
    res = await check_vehicle_camera(
        {"vehicle_name": "103", "company": "G1",
         "_scope_vehicles": ["103"], "_scope_identities": [[42, "sam_42", "103"]]},
        None, account_id=1, db=_DB([OSY, G1]))

    assert res.get("error"), res


@pytest.mark.asyncio
async def test_only_this_trucks_frame_is_fetched(monkeypatch, _stub):
    """The fan-out was the whole cost: a roster fetch per company, a
    JPEG per truck, and safety videos through ffmpeg for whatever the
    media API missed — to use one frame."""
    import features.cameras.service as svc

    asked = {}

    async def _snaps(account_id, days=3, *, vehicle_ids=None, company=None):
        asked["vehicle_ids"] = vehicle_ids
        asked["company"] = company
        return [_snap("sam_42")]

    monkeypatch.setattr(svc, "get_dashcam_snapshots", _snaps, raising=False)
    await check_vehicle_camera(
        {"vehicle_name": "103", "company": "OSY"}, None,
        account_id=1, db=_DB([OSY, G1]))

    assert asked["vehicle_ids"] == ["sam_42"], asked
    assert asked["company"] == "OSY", asked


@pytest.mark.asyncio
async def test_a_truck_with_no_provider_link_still_asks(monkeypatch, _stub):
    """A manual or TMS-only truck has no telematics_ref — narrowing by
    id is impossible, and the tool must still ask rather than refuse."""
    import features.cameras.service as svc

    asked = {}

    async def _snaps(account_id, days=3, *, vehicle_ids=None, company=None):
        asked["vehicle_ids"] = vehicle_ids
        return []

    monkeypatch.setattr(svc, "get_dashcam_snapshots", _snaps, raising=False)
    manual = _V(7, "", "888", "OSY")
    res = await check_vehicle_camera(
        {"vehicle_name": "888"}, None, account_id=1, db=_DB([manual]))

    assert asked["vehicle_ids"] is None
    assert res.get("result"), res
