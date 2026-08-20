# src/preferences — per-user UI state (the SSOT)

Every setting the dashboard remembers **for one person** goes through here.
Backend half: [capabilities/preferences/CLAUDE.md](../../../../capabilities/preferences/CLAUDE.md).

```
registry.ts    THE SSOT — every key: type, default, scope, legacy name, guard
store.ts       observable store (outside React) + the SyncBackend seam
local.ts       localStorage adapter — canonical `4truck.pref.` + legacy migration
remote.ts      the account backend (bulk read, per-key debounced writes)
usePreference  React hook (useSyncExternalStore)
usage.ts       how many bytes the user's preferences occupy
PreferencesSync.tsx      mounts inside AuthProvider; attaches/detaches sync
StoredPreferencesCard.tsx  the /profile card: size · Cloud|Local · Reset all
registry.test.ts         FROZEN-KEY guard (see below)
```

## THE RULE — does my setting belong here?

- **If any BACKEND path reads the value to act on it** (DND gating alerts,
  timezone in bot messages, language in emails), **or it affects anyone but
  the current user → a typed column / feature table.**
- **If it only changes how THIS user's screen renders → here.**

So `PUT /user/preferences` (language · timezone · DND) stays typed and
separate — the bot and notification router consume it. Account-level
`work_hours` stays in its feature table. Session tokens, transient drafts
and data caches are **not** preferences.

Worked examples of things that LOOK like preferences but aren't — don't
"finish the migration" by moving these in:

| Storage | What it really is |
|---|---|
| `poi_v2_<layer>_<bbox>` (`hooks/usePoiLayers`) | a map-tile **cache**: `{ts, features}` with 30 min / 2 hr TTLs and eviction |
| `4truck_dispatch_last_ack_iso` | an acknowledgement **timestamp** — operational state for "what's new since" |
| `api/client` TOKEN_KEY, `AuthContext` | session/auth |
| AI `attachmentStore` / `thoughtStore` | transient data caches |
| public apply / carrier-intake drafts | form drafts with no logged-in user |
| `i18nextLng` | owned by the i18n library; language is a typed profile field |

The test: a preference has a **default**, a user **chooses** it, and losing
it is an annoyance. A cache has a TTL and losing it costs a re-fetch.

## Using it

```tsx
const { value, setValue, resetValue } = usePreference('notif.position');
```
Types come from the registry — no generics at the call site. Outside React
(module init, plain functions): `preferences.get('theme')` / `.set(...)`.

## Adding a preference

One entry in `registry.ts` + append its key to `FROZEN_KEYS` in
`registry.test.ts`:

```ts
'maintenance.viewMode': def<MaintenanceViewMode>({
  default: 'list',
  scope: 'synced',
  legacyKeys: ['4truck.maintenance.viewMode'],   // old localStorage key
  fromLegacy: (raw) => (raw === 'calendar' ? 'calendar' : 'list'),
  sanitize: oneOf(['list', 'calendar']),
  note: 'Maintenance tasks as a list or a calendar.',
}),
```

- **`scope`** — `device` = a property of THIS screen (window geometry,
  noise level on a wall display vs a cab tablet, preview affordances).
  `synced` = a property of the PERSON. When unsure, read the call site's
  own comments before deciding: several existing keys are deliberately
  device-scoped for real operational reasons.
- **`sanitize`** — the enum whitelist / numeric clamp / partial-object
  completion each call site used to hand-roll. Runs on values from
  storage, other tabs, AND the server: all three are untrusted input.
- **`fromLegacy`** — the pre-service sites were inconsistent (`JSON`,
  bare `'1'`, bare ints, bare enum strings), so a raw legacy string may
  need converting.

## Keys are FROZEN

A key is the address of real user data. Renaming one does not error — the
entry stops resolving and the user **silently loses** their saved state
(this is exactly the saved-tab data-loss bug from the DataGrid work).
`registry.test.ts` pins every key string and every `legacyKeys` entry, so
a rename is a red test. It has already caught real mistakes; do not
"tidy" it.

Legacy values are migrated **lazily** on first read and copied forward;
the old entry is deliberately **left in place** so a release rollback
loses nothing.

## The one sanctioned reader outside this service

`index.html`'s inline `theme-boot` script reads `4truck.pref.theme` (and
the legacy `dashboard-theme`) straight from `localStorage`. That is not a
violation of the rule above — it is the one place the rule *cannot* apply:
it runs before any module exists, to stamp the theme onto `<html>` before
the first paint. It is **read-only**, so `local.ts` remains the single
writer and keeps sole ownership of the copy-forward.
`src/test/themeBoot.test.ts` pins both halves — that the script agrees with
`applyTheme` on every valid value, and that it never writes. Anything else
that wants pre-hydration state must clear the same bar; the default answer
is still `usePreference`.

## Sync (Cloud vs Local)

- `prefs.syncEnabled` is the master switch, surfaced on `/profile`. It is
  necessarily `device` scope — a machine can't have its sync switched off
  *via* the sync channel.
- `PreferencesSync` attaches the remote backend only once auth resolves,
  and **detaches on logout** (otherwise the next user's writes would land
  on the previous user's rows).
- Reads are ONE bulk `GET /user/preferences/ui`; writes are per-key and
  debounced. Per-key (never one blob) is what stops two devices writing
  different preferences from clobbering each other.

## DataGrid's per-table keys

DataGrid stores a SET of preferences per table, so its keys aren't fixed:
`table.<id>.visibility|order|pinning|colWidths|groups|rowGroup|`
`aggregation|pageSize|views|defaultView`. They're declared once in
`TABLE_PARTS` and addressed through **`useTablePreference(tableId, part,
defaultValue?)`**; `tableKey()` is the only place the string is built and
`registry.test.ts` pins its output for every part.

Two DataGrid keys are NOT families — `table.density` and
`datagrid.savedTabCoachSeen` are one setting for every grid, so they're
fixed keys. (`defFor` needs TWO dots to treat a key as a family, which is
what stops `table.density` being read as table id "density".)

`useSyncLoaded()` answers "has the account's copy arrived?" — DataGrid
gates "apply the default tab exactly once" on it, replacing the per-key
`hydrated` flag the retired `useUserPreference` hook exposed. It resolves
immediately when syncing is off and even when the bulk read FAILS, so an
offline device can't hang waiting for it.
