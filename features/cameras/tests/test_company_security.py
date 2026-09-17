"""Unresolved camera ownership must not grant a restricted viewer access."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from features.cameras import router
from interfaces.api import deps

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


@pytest.mark.parametrize('mapping', [{}, {'v-b': 'B'}, {'v-b': ''}])
async def test_unknown_or_other_company_image_is_denied(monkeypatch, mapping):
    check = {'id': 1, 'vehicle_id': 'v-b', 'image_path': 'synthetic.jpg'}
    db = SimpleNamespace(get_camera_check_history=AsyncMock(return_value=[check]),
                         get_camera_check=AsyncMock(return_value=check))
    monkeypatch.setattr(router, 'get_user_company_codes', AsyncMock(return_value=['A']))
    monkeypatch.setattr(router, 'vehicle_company_map', AsyncMock(return_value=mapping))
    store = SimpleNamespace(get_by_id=lambda _: b'PRIVATE_MARKER')
    getter = AsyncMock(return_value=store)
    monkeypatch.setattr(router, 'get_object_storage_for_account', getter)
    with pytest.raises(HTTPException) as caught:
        await router.camera_check_image(1, user={'account_id': 42}, tenant_db=db)
    assert caught.value.status_code == 404
    getter.assert_not_awaited()


async def test_company_lookup_failure_cannot_become_unrestricted():
    db = SimpleNamespace(get_vehicle_state=AsyncMock(side_effect=RuntimeError('offline')),
                         get_vehicle_company_codes=AsyncMock(side_effect=RuntimeError('offline')))
    with pytest.raises(HTTPException) as caught:
        await deps.vehicle_company_map(42, db)
    assert caught.value.status_code == 503


async def test_allowed_image_remains_readable(monkeypatch):
    check = {'id': 1, 'vehicle_id': 'v-a', 'image_path': 'synthetic.jpg'}
    db = SimpleNamespace(get_camera_check_history=AsyncMock(return_value=[check]),
                         get_camera_check=AsyncMock(return_value=check))
    monkeypatch.setattr(router, 'get_user_company_codes', AsyncMock(return_value=['A']))
    monkeypatch.setattr(router, 'vehicle_company_map', AsyncMock(return_value={'v-a': 'A'}))
    monkeypatch.setattr(router, 'get_object_storage_for_account', AsyncMock(
        return_value=SimpleNamespace(get_by_id=lambda _: b'ALLOWED_MARKER')))
    response = await router.camera_check_image(1, user={'account_id': 42}, tenant_db=db)
    assert response.body == b'ALLOWED_MARKER'
