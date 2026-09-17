"""Camera permissions precede pagination and use stable vehicle identity."""
import pytest

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


async def test_scope_precedes_limit_and_latest_uses_id(pg_db):
    a = await pg_db.create_account('Camera scope')
    b = await pg_db.create_account('Another tenant')
    async def save(aid, vid):
        return await pg_db.save_camera_check(aid, vid, '103', 'forward', 'OK', '', '', '', '')
    mine = await save(a.id, 'v-a')
    await save(a.id, 'v-b')
    await save(b.id, 'v-a')
    for latest in (False, True):
        rows = await pg_db.get_camera_check_history(a.id, limit=1, latest_only=latest, vehicle_ids=['v-a'])
        assert [r['id'] for r in rows] == [mine]
        assert await pg_db.get_camera_check_history(a.id, vehicle_ids=[]) == []
    assert len(await pg_db.get_camera_check_history(a.id, latest_only=True)) == 2
    assert await pg_db.get_camera_check(b.id, mine) is None


async def test_registry_ownership_survives_retirement_and_denies_ambiguity(pg_db):
    a = await pg_db.create_account('Ownership')
    b = await pg_db.create_account('Foreign ownership')
    # The storage mixin owns the registry; direct fixtures include retired rows.
    for aid, code, ref, active in [(a.id, 'A', 'v1', 0), (a.id, 'A', 'shared', 1),
                                   (a.id, 'B', 'shared', 1), (b.id, 'B', 'v1', 1)]:
        await pg_db._db.execute(
            'INSERT INTO vehicles (account_id, company_code, unit_number, vehicle_type, telematics_ref, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (aid, code, ref, 'truck', ref, active, pg_db._now(), pg_db._now()))
    mapping = await pg_db.get_vehicle_company_codes(a.id)
    assert mapping == {'v1': 'A', 'shared': ''}
