"""Perf-rig backend: an ISOLATED copy of the API on a scratch Postgres.

Owner rule: never measure against a real account.  This boots the real
application code against a throwaway docker Postgres, seeds a fake
fleet (12 dispatchers, 69 trucks, ~300 loads over one week), creates a
draft incentive run, and serves the API on 127.0.0.1:8010.  Loads are
served the way the test-suite serves them — by overriding
``loads_service.get_loads`` — so no TMS integration is touched.

Everything here lives in docker container `kpi-perf-rig-pg` and this
process; killing both leaves zero trace.
"""
import asyncio
import os
import random
import subprocess
import sys
import time

# ── env BEFORE any app import (fail-fast secrets, scratch object store) ──
SCRATCH = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ENCRYPTION_KEY", "")
# Random per boot — the rig's tokens must not be forgeable from the
# repo text (they only open the rig's own localhost API, but a fixed
# secret in a committed file is bad hygiene regardless).
import secrets as _secrets
os.environ["JWT_SECRET"] = _secrets.token_hex(32)
os.environ["OBJECT_STORE_ROOT"] = os.path.join(SCRATCH, "objstore")
PG_URL = "postgresql://rig:rig@127.0.0.1:55439/rig"

_root = SCRATCH
while _root != "/" and not os.path.isdir(os.path.join(_root, ".git")):
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)


def ensure_pg():
    r = subprocess.run(["docker", "ps", "-q", "-f", "name=kpi-perf-rig-pg"],
                       capture_output=True, text=True)
    if not r.stdout.strip():
        subprocess.run([
            "docker", "run", "-d", "--rm", "--name", "kpi-perf-rig-pg",
            "-e", "POSTGRES_PASSWORD=rig", "-e", "POSTGRES_USER=rig",
            "-e", "POSTGRES_DB=rig", "-p", "127.0.0.1:55439:5432",
            "postgres:16-alpine",
        ], check=True)
    import asyncpg

    async def wait():
        for _ in range(60):
            try:
                c = await asyncpg.connect(PG_URL)
                await c.close()
                return
            except Exception:
                await asyncio.sleep(0.5)
        raise RuntimeError("scratch postgres never became ready")
    asyncio.run(wait())


DISPATCHERS = [
    "Aaron Field", "Bella Grant", "Carlos Vega", "Dina Moss",
    "Erik Stone", "Fay Lund", "Gus Harmon", "Hana Cole",
    "Ivan Petrov", "Jade Wynn", "Kofi Mensah", "Lena Ortiz",
]
COMPANIES = ["CFT", "PTG", "RMR"]
CITIES = [
    "Stockton, California", "Columbia, Maryland", "El Paso, Texas",
    "Nashville, Tennessee", "Orlando, Florida", "Harrisburg, Pennsylvania",
    "Elkhart, Indiana", "Denver, Colorado", "Gulfport, Mississippi",
    "Marysville, Ohio", "Flagstaff, Arizona", "Cheraw, South Carolina",
]
WEEK = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13",
        "2026-08-14", "2026-08-15", "2026-08-16"]


def build_loads():
    """69 units across 12 dispatchers, deterministic (seeded RNG)."""
    rng = random.Random(42)
    loads = []
    unit_no = 100
    n = 0
    for di, disp in enumerate(DISPATCHERS):
        units = 6 if di < 9 else 5  # 9*6 + 3*5 = 69
        for _ in range(units):
            unit_no += rng.randint(1, 9)
            unit = str(unit_no)
            company = COMPANIES[unit_no % len(COMPANIES)]
            for _ in range(rng.randint(2, 5)):
                pi = rng.randint(0, 6)
                span = rng.choice([0, 0, 1, 1, 2, 4])
                rate = rng.randint(8, 75) * 100
                miles = int(rate / rng.uniform(2.0, 3.5))
                n += 1
                loads.append({
                    "status": "delivered",
                    "load_number": f"L{n:04}",
                    "dispatcher_user_id": 100 + di,
                    "dispatcher_name": disp,
                    "company_code": company,
                    "vehicle_unit": unit,
                    "total_rate": rate,
                    "loaded_miles": miles,
                    "empty_miles": rng.randint(50, 300),
                    "pickup_date": WEEK[pi],
                    "delivery_date": WEEK[min(pi + span, 6)] if span == 0
                        else f"2026-08-{10 + pi + span:02}",
                    "pickup_location": rng.choice(CITIES),
                    "delivery_location": rng.choice(CITIES),
                })
    return loads


LOADS = build_loads()

LADDER_VALUES = {
    "model": "ladder", "calc_cadence": "weekly",
    "exception_cap_pct": 4.0,
    "floor_weekly_gross": 4000.0, "floor_rpm": 1.5,
}
TIERS = [
    {"min_rpm": 2.0, "pct": 1.0},
    {"requires_target": True, "min_rpm": 2.0, "pct": 2.0},
    {"requires_target": True, "min_rpm": 2.5, "pct": 2.5},
    {"requires_target": True, "min_rpm": 2.75, "pct": 2.75},
    {"requires_target": True, "min_rpm": 3.0, "pct": 3.25},
]


async def main():
    import adapters.storage.core as _core
    _core._DATABASE_URL = PG_URL
    from adapters.storage import Database
    from capabilities.permissions.roles import Role

    db = Database("ignored_pg_branch", pool_size=4)
    await db.initialize()

    acct = await db.create_account("Perf Rig Co")
    owner = await db.create_user(1, acct.id, role=Role.OWNER)
    comp_ids = {}
    for code in COMPANIES:
        c = await db.add_company(acct.id, code, display_name=f"{code} LLC")
        comp_ids[code] = c.id

    await db.set_kpi_incentive_config(acct.id, LADDER_VALUES, TIERS,
                                      updated_by=owner.id)
    await db.set_kpi_company_targets(
        acct.id, {cid: 8000.0 for cid in comp_ids.values()})

    # Loads come from the override, exactly like the test suite does it.
    from features.kpi.dispatch import runs as runs_mod

    async def fake_get_loads(account_id, **kw):
        return [dict(l) for l in LOADS]
    runs_mod.loads_service.get_loads = fake_get_loads

    import infra.platform as _cp
    _cp._db = db

    run_id = await runs_mod.create_run(
        acct.id, period_start="2026-08-10", period_end="2026-08-16",
        created_by=owner.id)
    detail = await runs_mod.get_run_detail(acct.id, run_id)
    print(f"RIG SEEDED: run={run_id} rows={len(detail['rows'])} "
          f"dispatchers={len(detail['payouts'])}", flush=True)

    from interfaces.api.auth import create_jwt
    token = create_jwt(1, acct.id, "owner", user_id=owner.id,
                       remember_me=True)
    with open(os.path.join(SCRATCH, "rig_token.txt"), "w") as f:
        f.write(token)
    print("TOKEN WRITTEN", flush=True)

    from interfaces.api.app import create_api
    app = create_api()

    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=8010,
                            log_level="warning")
    server = uvicorn.Server(config)
    print("RIG API READY on 127.0.0.1:8010", flush=True)
    await server.serve()


if __name__ == "__main__":
    ensure_pg()
    asyncio.run(main())
