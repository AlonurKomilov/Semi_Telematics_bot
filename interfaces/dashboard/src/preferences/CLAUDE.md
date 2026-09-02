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
appearance.ts  the device-value / synced-default seam (theme + size)
SizeCard.tsx   the /profile Size panel and the cross-device switch
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

**A READ may leave the old entry. A RESET may not.** `removePref` — whose
only caller is `reset()` — sweeps the whole legacy chain as well as the
canonical key, and does it **verbatim**, never through `lsKey()`, because
`readLegacy` reads them raw and the chain mixes both forms (`mods.theme`
carries the prefixed `4truck.pref.theme` AND the pre-prefix
`dashboard-theme`).

Without that sweep a reset resets nothing: the next `readPref` falls
through to the legacy entry, copies it forward, and the value is back.
The in-memory `values` map hides it — `store.get` serves the default it
was just handed, so the setting only springs back on the next page load,
and the pre-paint script repaints it on the FIRST frame. That was live
for **17 keys**, every one of them a "Reset all preferences" that left
the user's old value in place. Guard: `resetLegacy.test.ts`.

**So a `legacyKeys` entry now costs one more thing to get right.** It is
an address the reset path must also clear, which is a second reason to
add one deliberately rather than defensively.

### `legacyKeys` covers TWO stores, and it only used to cover one

`legacyKeys` began as a **localStorage-only** mechanism: `readPref` falls
back through the chain in `local.ts`, but `remote.ts` PUTs the registry
key **verbatim** as the server row key (`/user/preferences/ui/{key}`) and
adoption looked rows up by that same string with no alias table. So
renaming a `synced` key orphaned its server row, and the browser fallback
rescued only the one machine that still held the old entry — sign in
anywhere else and the value was silently the default. `sound.pack` →
`mods.sound.pack` did exactly that, and nothing failed.

`store.ts` now resolves server rows through the same chain: a legacy
entry that starts with `LS_PREFIX` **is** a former registry key (because
`lsKey(k) = LS_PREFIX + k` is the only place a canonical storage string
is built), so stripping the prefix recovers the row key. Legacy rows are
adopted BEFORE canonical ones, so an account carrying both spellings
lands on the canonical value whatever order the bulk read returns.

Two consequences when you add a `legacyKeys` entry:

- **With the `LS_PREFIX`** (`4truck.pref.sound.pack`) you are declaring a
  former REGISTRY key. Both stores migrate. `serverLegacy.test.ts`
  asserts every one of these resolves.
- **Without it** (`dashboard-theme`, `4truck.table.density`) you are
  declaring a pre-service raw localStorage key. No server row ever
  existed under it, and inventing a mapping would be wrong — the guard
  asserts these stay unmapped.

Adoption is **read-only**: it never writes the canonical row back. The
next time the user changes the preference it PUTs under the canonical key
by itself, and a boot that silently rewrites server rows is a surprise
nobody asked for.

**Renaming an already-renamed key: the array's ORDER is the tie-break.**
Two prefixed entries mean two server rows resolve to one canonical key,
and a bulk read carries no order guarantee and no timestamp — so before
this rule, whichever came later in the response silently won. `readPref`
already walks `legacyKeys` in order, so index 0 is the most recent former
spelling; server rows now answer to that same precedence. Put the newer
spelling FIRST. `serverLegacy.test.ts` pins the rule on synthetic chains
and carries a tripwire that fails the day a real key gains a second
former spelling, because that is when the end-to-end path stops being
behaviour-neutral and starts needing its own test.

## The one sanctioned reader outside this service

`index.html`'s inline `theme-boot` script reads `4truck.pref.mods.theme`
— falling back to `4truck.pref.theme`, then the pre-service
`dashboard-theme` — plus `4truck.pref.size`, straight from
`localStorage`. Every link of that chain has its own test in
`themeBoot.test.ts`; they did not, and deleting the canonical read left
all 47 tests green while every user's first painted frame reverted to
their pre-rename theme. That is not a
violation of the rule above — it is the one place the rule *cannot* apply:
it runs before any module exists, to stamp the theme onto `<html>` before
the first paint. It is **read-only**, so `local.ts` remains the single
writer and keeps sole ownership of the copy-forward.
`src/test/themeBoot.test.ts` pins both halves — that the script agrees with
`applyTheme` AND `applySize` on every valid value (including the size clamp,
which is written twice and would otherwise drift silently), and that it
never writes. Anything else
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
