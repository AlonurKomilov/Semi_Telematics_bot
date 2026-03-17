"""
Automated test suite for Semi Telematics Bot.

Covers:
  - Encryption module (encrypt/decrypt, passthrough, error cases)
  - Database layer (CRUD for accounts, companies, users, invites)
  - Permissions / RBAC (role→feature matrix, system owner detection)
  - Invite flow (create, redeem, expiry, double-use prevention)
  - Multi-tenant isolation
  - Keyboard UI (main menu, sub-menus, back navigation, role visibility)
  - Encryption migration (plaintext→encrypted key migration)

Run:
    pytest tests/ -v
"""
