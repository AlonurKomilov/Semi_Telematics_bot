-- RLS administration setup — DBA-driven (requires superuser).
--
-- WHAT THIS DOES
--   1. Creates the ``4truck_admin`` role with BYPASSRLS, used by
--      legitimate cross-tenant operations (admin endpoints, billing
--      rollups, telemetry warehouse fanout) when the app needs to read
--      across every account.
--   2. Grants it the same table permissions as the application's
--      regular user (whose name is whatever your DATABASE_URL specifies
--      — typically ``4truck``).  Adjust the role name on the
--      ``GRANT ... TO 4truck_admin`` lines below if your app user is
--      named differently.
--
-- WHY IT'S A SEPARATE SCRIPT (not a migration)
--   Postgres role creation requires ``CREATEROLE`` (or superuser) which
--   most application DB users don't have, so the migration system can't
--   run this.  Hand it to a DBA / run it once via ``psql -U postgres``.
--
-- IDEMPOTENT
--   Re-run is safe: the role-creation block uses DO/IF NOT EXISTS,
--   GRANTs are additive.
--
-- USAGE
--   sudo -u postgres psql -d 4truck -f scripts/sql/rls_admin_role.sql
--
-- WHO USES THE ROLE
--   The role is currently unused at runtime — Tier 4 Phase 2 (full pool
--   refactor) will add a second connection pool authenticated as
--   ``4truck_admin`` for cross-tenant queries.  Until then, cross-tenant
--   code paths loop and switch ``app.account_id`` per account (see
--   ``Database.with_account()`` and the scheduler isolation wrapper).

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '4truck_admin') THEN
        CREATE ROLE "4truck_admin" WITH NOLOGIN BYPASSRLS;
        RAISE NOTICE '4truck_admin role created with BYPASSRLS';
    ELSE
        -- Make sure BYPASSRLS is on even if a previous run created the
        -- role without it.  ALTER ROLE is idempotent.
        ALTER ROLE "4truck_admin" BYPASSRLS;
        RAISE NOTICE '4truck_admin role already exists, BYPASSRLS verified';
    END IF;
END
$$;

-- Grant SELECT/INSERT/UPDATE/DELETE on every public.* table to the admin
-- role so it can do the same operations as the regular app user without
-- per-table RLS filtering.  Future tables created after this point need
-- their own grants — see the RLS rollout runbook for the ALTER DEFAULT
-- PRIVILEGES one-liner that automates this.
GRANT USAGE ON SCHEMA public TO "4truck_admin";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "4truck_admin";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "4truck_admin";

-- Make future tables inherit the same grants.  Adjust ``4truck`` below
-- to whatever role owns / creates new tables in your environment.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "4truck_admin";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO "4truck_admin";

COMMIT;
