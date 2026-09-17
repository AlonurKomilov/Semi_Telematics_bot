# Security fixes: session cutover

This change closes inconsistent Bearer/cookie enforcement, static-file traversal,
stale customer privileges, and unresolved company access.

## Deployment

1. Apply the additive `migrate_user_auth_version` migration before the updated API
   serves requests. Normal database initialization runs it automatically. It adds
   `users.auth_version` and an idempotent trigger; no customer data is deleted.
2. Drain API traffic while replacing all API workers, and update/restart the bot
   process too. Old workers do not enforce the new version and must not remain in
   service during the cutover. The bot also issues versioned login approvals.
3. Existing tokens without `auth_version` require a new sign-in, including
   extension reconnects. Do not exempt legacy customer tokens from validation.
4. Verify normal email, Google, Telegram, bot approval, operator, and extension
   login. Verify both `/api` and `/api/v1`, through nginx and the private origin.
   Test traversal only with a temporary marker, never a secret or customer file.

No JWT signing-key rotation is needed solely for version-based invalidation.
Exposure found during a separate live investigation may require its own response.

## Security behavior

- Middleware and routes share one request-scoped identity, including rejection.
- Each customer request reads current user role/status/version and durable session
  revocation from Postgres. A failed read gives 503; Redis cannot override it.
- Role, manager/owner status, activation, password, email, account, Telegram, and
  Google identity changes increment the version and revoke recorded sessions in
  the same database transaction. Ordinary profile preferences do not sign out users.
- Password/credential changes require a new sign-in on all devices. A refreshed
  token retains the version it validated; a concurrent change cannot revive it.
- Row-less operators remain allowlist-checked and limited to system routes.
- Restricted company viewers cannot see unresolved/ambiguous vehicle ownership.
  Unrestricted staff can assign those records. Lookup errors give a temporary error.
- Camera ownership comes from the durable registry, includes retired vehicles,
  and is applied before latest-selection and pagination.

## Verification

Run `pytest -m security` plus the affected Google, extension, API, and quarantine
suites against isolated test databases. Disable `tests._suite_report` for local
runs that should not publish results (`-p no:tests._suite_report`).

The focused regression files live in `interfaces/api/tests/`,
`features/cameras/tests/test_company_security.py`, and
`adapters/storage/tests/test_auth_version.py` / `test_camera_company_scope.py`.

Watch authentication 401/503 responses during rollout and confirm an affected
user can sign in again. Do not roll back to the vulnerable authentication resolver
as an availability workaround; diagnose failed database/migration or login checks.
