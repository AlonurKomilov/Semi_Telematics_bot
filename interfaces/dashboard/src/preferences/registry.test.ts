import { describe, it, expect, beforeEach } from 'vitest';

import {
  DEFS, TABLE_PARTS, tableKey, defFor, type PrefKey, type TablePart,
} from './registry';
import { LS_PREFIX, readPref, writePref } from './local';
import * as store from './store';

/**
 * FROZEN KEYS — the point of this file.
 *
 * A preference key is the address of real user data (in localStorage now,
 * in ``user_preferences`` rows after Phase 2).  Renaming one doesn't
 * error: the old entry simply stops resolving and the user silently loses
 * their saved state.  This snapshot makes a rename a RED TEST instead.
 *
 * Adding a key here is expected (append it).  CHANGING or REMOVING a
 * string means you are about to orphan data — migrate it via
 * ``legacyKeys`` instead.
 */
const FROZEN_KEYS: readonly string[] = [
  'prefs.syncEnabled',
  'integrations.cardOpen',
  'ai.thoughtNoteDismissed',
  'theme',
  // Size replaced the theme's `density` FIELD. `theme` itself is
  // untouched — removing a field is safe where removing a key is not.
  'size',
  'appearance.followMe',
  'appearance.default',
  'loads.rangeDays',
  'sidebar.collapsed',
  'notif.bannerLevel',
  'notif.position',
  'livemap.overlay.utilheat',
  'livemap.overlay.companycolors',
  'notifications.center.filter',
  'maintenance.viewMode',
  'table.density',
  'datagrid.savedTabCoachSeen',
  // One act: the strip shrank to a line and is still on screen, so
  // the client writes it like any display setting.  A sibling
  // `callout.dismissed` was removed rather than frozen — freezing
  // protects a key USERS HAVE DATA UNDER, and nothing ever wrote this
  // one: it was server-owned, and its endpoint was unreachable from
  // every screen.  A frozen key with no data is a name nobody may
  // reuse, protecting nothing.
  'callout.collapsed',
  'onboarding.dismissed',
  'alerts.routingNudgeDismissed',
  'invites.lastChannel',
  'dispatch.soundOn',
  'assistant.panelWidth',
  // One-release pointer telling users config moved to the gear.
  // Remove together with ConfigMovedNotice.tsx and its call sites.
  'config.moved_notice_dismissed',
  'pivot.panelWidth',
  'permissions.role',
  'assistant.expanded',
  'roleView.previewAsManager',
  'roleView.activeView',
  'kpi.incentiveRunView',
];

/** Legacy keys we promised to keep reading.  Dropping one abandons the
 *  data of anyone who hasn't opened the app since the migration. */
const FROZEN_LEGACY: Readonly<Record<string, readonly string[]>> = {
  'theme': ['dashboard-theme'],
  'sidebar.collapsed': ['sidebar.collapsed'],
  'notif.bannerLevel': ['notif.bannerLevel'],
  'notif.position': ['notif.position'],
  'maintenance.viewMode': ['4truck.maintenance.viewMode'],
  'table.density': ['4truck.table.density'],
  'onboarding.dismissed': ['4truck.onboarding.dismissed'],
  'alerts.routingNudgeDismissed': ['tg_routing_nudge_dismissed'],
  'invites.lastChannel': ['invites.lastChannel'],
  'dispatch.soundOn': ['4truck_dispatch_sound_on'],
  'assistant.panelWidth': ['assistant.panelWidth'],
  'assistant.expanded': ['assistant.expanded'],
  'roleView.previewAsManager': ['roleView.previewAsManager'],
  'roleView.activeView': ['roleView.activeView'],
};

describe('preferences registry — frozen keys', () => {
  it('exposes exactly the frozen key set', () => {
    expect(Object.keys(DEFS).sort()).toEqual([...FROZEN_KEYS].sort());
  });

  it('still reads every promised legacy key', () => {
    for (const [key, legacy] of Object.entries(FROZEN_LEGACY)) {
      expect(DEFS[key as PrefKey].legacyKeys ?? []).toEqual(legacy);
    }
  });

  it('has no legacy alias for keys that were already server-synced', () => {
    // livemap.* and notifications.center.filter came from useUserPreference
    // under these EXACT names, so their server rows already match — adding
    // a legacyKeys entry would be wrong (nothing to migrate from).
    for (const k of ['livemap.overlay.utilheat', 'livemap.overlay.companycolors',
                     'notifications.center.filter'] as PrefKey[]) {
      expect(DEFS[k].legacyKeys).toBeUndefined();
    }
  });

  it('keeps the canonical storage prefix stable', () => {
    // Shared with the pre-existing useUserPreference cache — changing it
    // would make Phase 2 re-download every synced preference.
    expect(LS_PREFIX).toBe('4truck.pref.');
  });

  it('declares a scope and a default for every key', () => {
    for (const key of Object.keys(DEFS) as PrefKey[]) {
      const d = DEFS[key];
      expect(['device', 'synced']).toContain(d.scope);
      expect(d.default).toBeDefined();
    }
  });
});

describe('legacy migration', () => {
  beforeEach(() => {
    localStorage.clear();
    store.resetAll();
    localStorage.clear();   // resetAll writes removals; start clean
  });

  it('adopts a legacy JSON value and copies it forward', () => {
    // A stored `density` is a retired field: the sanitizer rebuilds the
    // object field by field, so it is dropped rather than carried forward.
    localStorage.setItem('dashboard-theme', JSON.stringify({ color: 'light', density: 'compact', radius: 'sharp' }));
    expect(readPref('theme')).toEqual(
      { mode: 'light', accent: 'blue', radius: 'sharp', color: 'light' });
    // Copied forward under the canonical key, so the next read is direct.
    expect(JSON.parse(localStorage.getItem(`${LS_PREFIX}theme`)!)).toEqual(
      { mode: 'light', accent: 'blue', radius: 'sharp', color: 'light' });
    // Legacy entry deliberately left in place (roll-back safety).
    expect(localStorage.getItem('dashboard-theme')).not.toBeNull();
  });

  it("adopts legacy '1'/'0' booleans that were never JSON", () => {
    localStorage.setItem('sidebar.collapsed', '1');
    expect(readPref('sidebar.collapsed')).toBe(true);
  });

  it('adopts a legacy bare enum string', () => {
    localStorage.setItem('notif.position', 'bottom-center');
    expect(readPref('notif.position')).toBe('bottom-center');
  });

  it('falls back to the registry default when nothing is stored', () => {
    expect(readPref('notif.bannerLevel')).toBe('critical');
    expect(readPref('assistant.panelWidth')).toBe(420);
  });

  it('prefers the canonical value over a stale legacy one', () => {
    localStorage.setItem('dashboard-theme', JSON.stringify({ color: 'light', density: 'compact', radius: 'sharp' }));
    writePref('theme', { mode: 'dark', accent: 'green', radius: 'pill', color: 'dark-green' });
    expect(readPref('theme')).toEqual(
      { mode: 'dark', accent: 'green', radius: 'pill', color: 'dark-green' });
  });
});

describe('store semantics', () => {
  beforeEach(() => {
    localStorage.clear();
    store.resetAll();
    localStorage.clear();
  });

  it('notifies subscribers of the key that changed only', () => {
    let themeHits = 0;
    let sidebarHits = 0;
    const offA = store.subscribe('theme', () => { themeHits += 1; });
    const offB = store.subscribe('sidebar.collapsed', () => { sidebarHits += 1; });

    store.set('theme', { color: 'light', radius: 'rounded' });
    expect(themeHits).toBe(1);
    expect(sidebarHits).toBe(0);

    offA(); offB();
  });

  it('does not notify when a primitive value is unchanged', () => {
    let hits = 0;
    const off = store.subscribe('notif.position', () => { hits += 1; });
    store.set('notif.position', 'bottom-right');
    store.set('notif.position', 'bottom-right');   // same value → no-op
    expect(hits).toBe(1);
    off();
  });

  it('supports functional updates', () => {
    store.set('assistant.panelWidth', 400);
    store.set('assistant.panelWidth', (w) => w + 20);
    expect(store.get('assistant.panelWidth')).toBe(420);
  });

  it('reset() restores the default and clears storage', () => {
    store.set('notif.position', 'bottom-right');
    store.reset('notif.position');
    expect(store.get('notif.position')).toBe('top-right');
    expect(localStorage.getItem(`${LS_PREFIX}notif.position`)).toBeNull();
  });

  it('adoptRaw() ignores unknown keys (cross-tab noise)', () => {
    expect(() => store.adoptRaw('not.a.pref', '"x"')).not.toThrow();
  });

  it('rejects a stale enum member and falls back to the default', () => {
    // A value written by an older build ('none' was renamed to 'off').
    localStorage.setItem(`${LS_PREFIX}notif.bannerLevel`, JSON.stringify('none'));
    expect(readPref('notif.bannerLevel')).toBe('critical');
  });

  it('clamps a stored panel width that is out of range', () => {
    localStorage.setItem(`${LS_PREFIX}assistant.panelWidth`, JSON.stringify(99999));
    // 680 = PANEL_W_MAX, matching clampPanelW in AssistantContext.
    expect(readPref('assistant.panelWidth')).toBe(680);
    localStorage.setItem(`${LS_PREFIX}assistant.panelWidth`, JSON.stringify(10));
    expect(readPref('assistant.panelWidth')).toBe(320);
  });

  it('completes a partial stored theme from the default', () => {
    // Written before a field existed / hand-edited.
    localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify({ color: 'light' }));
    expect(readPref('theme')).toEqual(
      { mode: 'light', accent: 'blue', radius: 'rounded', color: 'light' });
  });

  it('adoptRaw() rejects an invalid cross-tab value', () => {
    store.set('notif.position', 'bottom-right');
    store.adoptRaw('notif.position', JSON.stringify('somewhere-else'));
    expect(store.get('notif.position')).toBe('bottom-right');   // unchanged
  });

  it('adoptRaw() updates a known key without echoing to storage', () => {
    localStorage.clear();
    store.adoptRaw('theme', JSON.stringify({ color: 'light', radius: 'rounded' }));
    expect(store.get('theme')).toEqual(
      { mode: 'light', accent: 'blue', radius: 'rounded', color: 'light' });
    // Came from another tab / the server — must not be written back.
    expect(localStorage.getItem(`${LS_PREFIX}theme`)).toBeNull();
  });
});

/**
 * FROZEN FAMILY KEYS — the DataGrid per-table addresses.
 *
 * These hold column layouts and SAVED TABS.  ``tableKey`` is the only
 * place they're built, so pinning its output here is what stops a rename
 * from silently wiping a user's tabs (the exact bug this project already
 * hit once).
 */
const FROZEN_TABLE_KEYS: Readonly<Record<string, string>> = {
  visibility:  'table.my-grid.visibility',
  order:       'table.my-grid.order',
  pinning:     'table.my-grid.pinning',
  colWidths:   'table.my-grid.colWidths',
  groups:      'table.my-grid.groups',
  rowGroup:    'table.my-grid.rowGroup',
  aggregation: 'table.my-grid.aggregation',
  pageSize:    'table.my-grid.pageSize',
  // The original suffixes, kept through the view→tab rename.
  views:       'table.my-grid.views',
  defaultView: 'table.my-grid.defaultView',
  pivot:       'table.my-grid.pivot',
};

describe('preferences registry — key families', () => {
  it('builds exactly the historical per-table key strings', () => {
    for (const [part, expected] of Object.entries(FROZEN_TABLE_KEYS)) {
      expect(tableKey('my-grid', part as TablePart)).toBe(expected);
    }
  });

  it('covers every declared family part', () => {
    expect(Object.keys(TABLE_PARTS).sort())
      .toEqual(Object.keys(FROZEN_TABLE_KEYS).sort());
  });

  it('resolves a def for family keys and rejects strangers', () => {
    expect(defFor('table.loads.views')).toBe(TABLE_PARTS.views);
    expect(defFor('table.vehicle-inventory-fleet.order')).toBe(TABLE_PARTS.order);
    // A fixed key still wins.
    expect(defFor('theme')).toBe(DEFS.theme);
    // Not ours → ignored (this is what keeps unrelated storage noise safe).
    expect(defFor('table.loads.somethingElse')).toBeNull();
    expect(defFor('poi_v2_repair_1_2')).toBeNull();
    expect(defFor('nonsense')).toBeNull();
  });

  it('never mistakes the fixed table.density key for a family key', () => {
    // 'table.density' is a single-dot key; the family pattern needs two.
    // It must resolve to its own FIXED def — never to a table part (which
    // would give every grid a per-table "density" row instead of the one
    // shared setting).
    expect(defFor('table.density')).toBe(DEFS['table.density']);
    expect(Object.values(TABLE_PARTS)).not.toContain(defFor('table.density'));
  });

  it('marks every table family as synced', () => {
    for (const part of Object.keys(TABLE_PARTS) as TablePart[]) {
      expect(TABLE_PARTS[part].scope).toBe('synced');
    }
  });
});

describe('resetAll sweeps family keys', () => {
  it('clears a per-table value that has no registry entry', () => {
    localStorage.clear();
    store.set('table.my-grid.views', [{ id: 'a', name: 'Mine' }]);
    expect(localStorage.getItem(`${LS_PREFIX}table.my-grid.views`)).not.toBeNull();
    store.resetAll();
    // Reset all must not leave saved tabs behind.
    expect(localStorage.getItem(`${LS_PREFIX}table.my-grid.views`)).toBeNull();
    expect(store.get('table.my-grid.views')).toEqual([]);
  });

  // ── The theme split's migration ─────────────────────────────────────
  // `sanitize` is the single funnel every read path goes through:
  // canonical key, legacy key, cross-tab adopt, and the synced
  // cross-device blob. It also rebuilds the stored object field by field
  // and DROPS anything it does not name — so a split that forgot the
  // migration branch would hand every dark-purple, dark-green and light
  // user a Dark Blue theme on their next page load, in silence.
  //
  // themeBoot.test.ts cannot catch this. It compares the pre-paint script
  // against `applyTheme`, and `applyTheme` takes an already-built
  // ThemeSetting — it never touches the sanitiser. Deleting the migration
  // from here left that file entirely green, which is why this exists.
  describe('the theme mode/accent migration', () => {
    const sanitize = DEFS.theme.sanitize!;

    it('turns every pre-split value into the right mode and accent', () => {
      const EXPECTED: Record<string, { mode: string; accent: string }> = {
        'dark-blue':   { mode: 'dark',  accent: 'blue' },
        'dark-purple': { mode: 'dark',  accent: 'purple' },
        'dark-green':  { mode: 'dark',  accent: 'green' },
        // Light's --primary has always been chromatic blue (index.css),
        // so blue is the accent it has always painted — not a fallback.
        'light':       { mode: 'light', accent: 'blue' },
      };
      for (const [color, want] of Object.entries(EXPECTED)) {
        expect(sanitize({ color, radius: 'pill' }), `stored ${color}`)
          .toEqual({ ...want, radius: 'pill', color });
      }
    });

    it('keeps a post-split value untouched', () => {
      expect(sanitize({ mode: 'light', accent: 'green', radius: 'sharp', color: 'light' }))
        .toEqual({ mode: 'light', accent: 'green', radius: 'sharp', color: 'light' });
    });

    it('re-derives the deprecated alias rather than trusting it', () => {
      // A stored object whose alias disagrees with its own mode/accent —
      // what a rolled-back build followed by a roll-forward can leave.
      // The alias is output, never input.
      expect(sanitize({ mode: 'dark', accent: 'green', radius: 'rounded', color: 'light' }))
        .toEqual({ mode: 'dark', accent: 'green', radius: 'rounded', color: 'dark-green' });
    });

    it('falls back to the default for a value that is neither shape', () => {
      expect(sanitize({ color: 'chartreuse', radius: 'pill' }))
        .toEqual({ mode: 'dark', accent: 'blue', radius: 'pill', color: 'dark-blue' });
    });

    it('cannot express light + a non-blue accent in the alias, and says so', () => {
      // The lossy direction is deliberate: a rollback keeps the MODE and
      // loses the accent, which is the better half to keep.
      const out = sanitize({ mode: 'light', accent: 'green', radius: 'pill' }) as { color: string };
      expect(out.color).toBe('light');
    });
  });
});
