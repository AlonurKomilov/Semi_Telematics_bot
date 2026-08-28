# capabilities/preferences — per-user UI state

The backend half of the preferences domain: an **opaque key-value store
scoped to one user**, so the dashboard's UI state follows an operator
across devices instead of living in one browser's localStorage.

```
capabilities/preferences/router.py     the 4 HTTP endpoints
capabilities/preferences/keys.py       key/prefix/value limits
adapters/storage/user_preferences.py   the mixin (all DB mixins live there)
adapters/storage/schema.py             user_preferences table DDL
interfaces/dashboard/src/preferences/  the frontend service (the consumer)
```

## THE RULE — does my new setting go here?

- **If any backend path reads the value to act on it** (DND gating
  alerts, timezone in bot messages, language in emails), **or it affects
  anyone but the current user → a typed column / feature table.**
- **If it only changes how THIS user's screen renders → here.**

Consequences of the rule, already in force:

- `PUT /user/preferences` (language · timezone · DND) stays a **typed**
  endpoint in `interfaces/api/routes/user.py` — the bot and the
  notification router consume those values. It is NOT part of this store.
- `work_hours` is **account-scoped** owner config
  (`adapters/storage/schedules.py`) and stays in its feature table. Only
  a user's *personal* choice about it could ever be a preference.
- Nothing in this store is read by backend logic. Values are written by
  the browser and handed back to it — treat them as opaque.

## Contract

| Method | Path | Notes |
|---|---|---|
| GET | `/user/preferences/ui?prefix=` | **bulk read** — one round-trip for every key; the dashboard's login-time load path |
| GET | `/user/preferences/ui/{key}` | `{"value": ""}` when unset — never 404, so first-read == fresh-default |
| PUT | `/user/preferences/ui/{key}` | upsert; body `{"value": "<json string>"}` |
| DELETE | `/user/preferences/ui/{key}` | used by "Reset to defaults" so cleared state stops syncing back |

- **Keys are opaque and frozen.** The frontend coins them
  (`table.<id>.views`, `notif.position`) and owns their meaning in its own
  registry. Renaming a key server-side or client-side **orphans that
  user's data** — there is no migration path, the row simply stops
  resolving. `capabilities/preferences/tests/test_preferences_routes.py` pins the paths for the
  same reason.
- Limits (`keys.py`): key `^[A-Za-z0-9._-]{1,200}$`, value ≤ 64 KB.
- **Tenant safety**: every handler resolves the user from the JWT and
  scopes by that `user_id`. No path can address another user's rows.

## Why it's a capability, not a feature

Preferences serve every feature and belong to no domain — same shelf as
`capabilities/permissions`, `notifications`, `localization`. It is a
tenant-serving capability, so it must NOT import from
`capabilities/platform/*` (guarded by `tests/test_layer_boundaries.py`).
