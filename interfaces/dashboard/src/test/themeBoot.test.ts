/**
 * The pre-paint script in index.html has ONE job: put <html> into exactly
 * the state ThemeProvider would put it in, but before the first paint.
 * It cannot import anything — it runs before any module — so it re-states
 * the registry's enums, defaults and clamp as literals.
 *
 * That duplication is the whole risk.  Nothing about a drifted copy is
 * loud: the app renders correctly after hydration and wrong before it,
 * for one frame, on a machine that isn't the author's.  So this suite
 * runs the REAL script (read out of index.html, not a copy) against the
 * REAL applyTheme/applySize and asserts they agree on every valid input,
 * on garbage, on nothing at all, and on the legacy key — plus the two
 * invariants the script must never break: it writes nothing, and it
 * stands down on the public apply host.
 *
 * The size half matters especially. Before it existed the guard read only
 * `dataset`, so a size stamp was entirely invisible to it: the two clamp
 * implementations could drift apart while the suite stayed green.
 */
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { applyTheme, applySize } from '../context/ThemeContext';
import {
  THEME_DEFAULT, THEME_COLORS, THEME_MODES, THEME_ACCENTS, THEME_RADII,
  THEME_MATERIAL_LIST,
  THEME_MOTION_LIST,
  themeColorAlias,
  SIZE_DEFAULT, SIZE_REGIONS, SIZE_MIN, SIZE_MAX, clampSize,
} from '../preferences/registry';
import { LS_PREFIX } from '../preferences/local';
import type { ThemeSetting, SizeSetting } from '../preferences/registry';

/** Locate index.html without `import.meta.url`: resolving against it here
 *  throws "The URL must be of scheme file" in this runner, so the path is
 *  built from the working directory instead.  Vitest runs from the
 *  directory holding vitest.config.ts (the dashboard root — that is what
 *  `npm test` and CI both do); the third candidate covers being invoked
 *  with the repo root as cwd. */
const INDEX_HTML = (() => {
  for (const dir of ['.', '..', 'interfaces/dashboard']) {
    const candidate = resolve(process.cwd(), dir, 'index.html');
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(`Cannot find index.html from ${process.cwd()}`);
})();

/** The script's source, lifted from index.html by its id. */
function bootScriptSource(): string {
  const html = readFileSync(INDEX_HTML, 'utf8');
  const m = /<script id="theme-boot">([\s\S]*?)<\/script>/.exec(html);
  if (!m) {
    throw new Error(
      'No <script id="theme-boot"> in index.html — the pre-paint theme '
      + 'stamp is gone, or it was renamed. Both are breaking changes: '
      + 'without it the first painted frame is the default theme at the '
      + 'default size.',
    );
  }
  return m[1];
}

interface Stamp {
  dark: boolean;
  theme?: string;
  accent?: string;
  radius?: string;
  vars: Record<string, string>;
}

/** Everything the two implementations are allowed to touch. Read as one
 *  object so a value one of them sets and the other does not shows up as
 *  a difference rather than going unnoticed. */
function stamp(): Stamp {
  const root = document.documentElement;
  const vars: Record<string, string> = {};
  for (const a of ['text', 'control', 'layout', 'panel']) {
    vars[`--size-${a}`] = root.style.getPropertyValue(`--size-${a}`);
  }
  for (const r of SIZE_REGIONS) {
    vars[`--size-region-${r}`] = root.style.getPropertyValue(`--size-region-${r}`);
  }
  return {
    dark: root.classList.contains('dark'),
    theme: root.dataset.theme,
    accent: root.dataset.accent,
    radius: root.dataset.radius,
    vars,
  };
}

/** Run it the way the browser would. */
function runBoot(): Stamp {
  // `new Function` on purpose: the point is to execute the SHIPPED
  // source, not a hand-copied approximation that can agree with the
  // test while disagreeing with the browser.
  new Function(bootScriptSource())();
  return stamp();
}

/** Same, with `location` shadowed — jsdom cannot navigate to another
 *  host, and the script only ever reads `.hostname` and `.search`. */
function runBootOnHost(hostname: string, search = ''): Stamp {
  new Function('location', bootScriptSource())({ hostname, search });
  return stamp();
}

/** What ThemeProvider produces for the same input. */
function runApply(theme: ThemeSetting, size: SizeSetting = SIZE_DEFAULT): Stamp {
  applyTheme(theme);
  applySize(size);
  return stamp();
}

function resetRoot() {
  const root = document.documentElement;
  root.classList.remove('dark');
  delete root.dataset.theme;
  delete root.dataset.accent;
  delete root.dataset.radius;
  root.removeAttribute('style');
}

let originalHref: string;

beforeEach(() => {
  localStorage.clear();
  resetRoot();
  originalHref = window.location.href;
});

afterEach(() => {
  localStorage.clear();
  resetRoot();
  window.history.replaceState({}, '', originalHref);
});

const storeTheme = (t: Partial<ThemeSetting>) =>
  localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify(t));
const storeSize = (s: Partial<SizeSetting>) =>
  localStorage.setItem(`${LS_PREFIX}size`, JSON.stringify(s));

describe('theme-boot ↔ applyTheme', () => {
  it('agrees on every valid stored theme', () => {
    for (const mode of THEME_MODES) {
      for (const accent of THEME_ACCENTS) {
      for (const radius of THEME_RADII) {
      // The material axis joins the sweep. Every axis the boot script
      // stamps has to be enumerated here, or the two implementations can
      // disagree on it and the first painted frame is one theme while
      // the app snaps to another after hydration.
      for (const material of THEME_MATERIAL_LIST) {
      for (const motion of THEME_MOTION_LIST) {
        const theme: ThemeSetting = {
          mode, accent, radius, material, motion, color: themeColorAlias(mode, accent),
        };

        resetRoot();
        storeTheme(theme);
        const booted = runBoot();

        resetRoot();
        const applied = runApply(theme);

        expect(booted, `boot disagrees for ${JSON.stringify(theme)}`).toEqual(applied);
      }
      }
      }
      }
    }
  });

  // The split's whole risk lives here. `sanitize` rebuilds the stored
  // object field by field and DROPS anything it does not name, so a
  // migration that exists in one of the two implementations and not the
  // other reverts users silently — and the boot script cannot import, so
  // there are always two implementations. The structural tests below
  // pass trivially on the new shape and would prove nothing about this.
  it('migrates a pre-split stored value identically in both implementations', () => {
    const LEGACY: Record<string, { mode: string; accent: string }> = {
      'dark-blue':   { mode: 'dark',  accent: 'blue' },
      'dark-purple': { mode: 'dark',  accent: 'purple' },
      'dark-green':  { mode: 'dark',  accent: 'green' },
      // Light's --primary has always been chromatic blue, so blue is the
      // accent it has always painted — not an arbitrary default.
      'light':       { mode: 'light', accent: 'blue' },
    };
    for (const color of THEME_COLORS) {
      for (const radius of THEME_RADII) {
        const { mode, accent } = LEGACY[color];

        resetRoot();
        // exactly what a browser holds from before the split
        localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify({ color, radius }));
        const booted = runBoot();

        resetRoot();
        const applied = runApply({
          mode: mode as ThemeSetting['mode'],
          accent: accent as ThemeSetting['accent'],
          radius,
          // A pre-split value has no material either; both sides must
          // land on the same default.
          material: 'solid',
          motion: 'default',
          color: themeColorAlias(mode as ThemeSetting['mode'], accent as ThemeSetting['accent']),
        });

        expect(booted, `boot mis-migrates the stored value ${color}/${radius}`).toEqual(applied);
        expect(booted.dark, `${color} lost its mode`).toBe(mode === 'dark');
        expect(booted.accent, `${color} lost its accent`).toBe(accent);
      }
    }
  });

  it('falls back to the registry default when nothing is stored', () => {
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(THEME_DEFAULT));
    // Guards the specific regression: the default is DARK, so a
    // storage-less first visit must not paint light.
    expect(booted.dark).toBe(true);
  });

  it.each([
    ['not json at all', 'definitely-not-json'],
    ['a JSON scalar', '"light"'],
    ['null', 'null'],
    ['an empty object', '{}'],
    ['values outside the enums', JSON.stringify({ color: 'chartreuse', radius: null })],
    ['a retired field only', JSON.stringify({ density: 'compact' })],
  ])('falls back to the registry default for %s', (_label, raw) => {
    localStorage.setItem(`${LS_PREFIX}theme`, raw);
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(THEME_DEFAULT));
  });

  it('completes a partially stored theme field by field, like the sanitizer', () => {
    // A pre-split partial: mode and accent both come from the migration.
    storeTheme({ color: 'light' });
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply({
      ...THEME_DEFAULT, mode: 'light', accent: 'blue', color: 'light',
    }));
  });

  it('reads the pre-service key when the canonical one is absent', () => {
    // Deliberately the OLD shape — this key predates the split by more
    // than it predates the preference service, so anything still in it
    // is guaranteed to need migrating.
    localStorage.setItem('dashboard-theme', JSON.stringify({ color: 'light', radius: 'sharp' }));
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply({
      mode: 'light', accent: 'blue', radius: 'sharp', material: 'solid',
      motion: 'default', color: 'light',
    }));
  });

  it('prefers the canonical key over the legacy one', () => {
    localStorage.setItem('dashboard-theme', JSON.stringify({ color: 'light', radius: 'sharp' }));
    const canonical: ThemeSetting = {
      mode: 'dark', accent: 'green', radius: 'pill', material: 'solid',
      motion: 'default', color: 'dark-green',
    };
    storeTheme(canonical);
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(canonical));
  });
});

describe('theme-boot ↔ applySize', () => {
  it.each([
    ['all axes at 1', { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} }],
    ['a global enlarge', { global: 1.25, text: 1, control: 1, layout: 1, panel: 1, regions: {} }],
    ['one axis apart', { global: 1, text: 1.5, control: 1, layout: 1.2, panel: 1, regions: {} }],
    ['global × axis', { global: 1.2, text: 1.25, control: 1, layout: 1, panel: 1.1, regions: {} }],
    ['a region set', { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: { tables: 1.3 } }],
    ['at the ceiling', { global: SIZE_MAX, text: SIZE_MAX, control: 1, layout: 1, panel: 1, regions: {} }],
  ])('agrees for %s', (_label, size) => {
    resetRoot();
    storeSize(size as SizeSetting);
    const booted = runBoot();

    resetRoot();
    const applied = runApply(THEME_DEFAULT, size as SizeSetting);

    expect(booted).toEqual(applied);
  });

  it('stamps the registry default when no size is stored', () => {
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(THEME_DEFAULT, SIZE_DEFAULT));
    expect(booted.vars['--size-layout']).toBe('1');
  });

  // THE reason this describe block exists. The clamp is written twice —
  // once in registry.ts, once as a literal in the boot script — and a
  // disagreement is invisible in the running app.
  it.each([
    ['below the floor', 0.5],
    ['far below', -3],
    ['just under', 0.999],
    ['at the floor', SIZE_MIN],
    ['mid range', 1.25],
    ['at the ceiling', SIZE_MAX],
    ['above the ceiling', 99],
    ['zero', 0],
  ])('clamps %s identically in both implementations', (_label, raw) => {
    resetRoot();
    storeSize({ global: raw } as unknown as SizeSetting);
    const booted = runBoot();

    resetRoot();
    const applied = runApply(THEME_DEFAULT, { ...SIZE_DEFAULT, global: clampSize(raw) });

    expect(booted).toEqual(applied);
  });

  it.each([
    ['a string', '"1.3"'],
    ['NaN-ish', '{"global":"abc"}'],
    ['an array', '[1,2]'],
    ['not json', 'nope'],
  ])('falls back to unscaled for %s', (_label, raw) => {
    localStorage.setItem(`${LS_PREFIX}size`, raw);
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(THEME_DEFAULT, SIZE_DEFAULT));
  });

  it('leaves an unset region unstamped rather than writing 1', () => {
    storeSize({ ...SIZE_DEFAULT, regions: { tables: 1.2 } });
    const booted = runBoot();
    expect(booted.vars['--size-region-tables']).toBe('1.2');
    expect(booted.vars['--size-region-navigation']).toBe('');
  });
});

describe('theme-boot invariants', () => {
  it('writes nothing — local.ts owns the copy-forward', () => {
    localStorage.setItem('dashboard-theme', JSON.stringify({ color: 'light', radius: 'sharp' }));
    const before = { ...localStorage };
    runBoot();
    expect({ ...localStorage }).toEqual(before);
    expect(localStorage.getItem(`${LS_PREFIX}theme`)).toBeNull();
  });

  it('stands down on the ?apply preview query', () => {
    window.history.replaceState({}, '', '/?apply');
    storeTheme({ color: 'dark-green', radius: 'pill' });
    storeSize({ ...SIZE_DEFAULT, global: 1.4 });
    const booted = runBoot();
    // Untouched: applyPublicFormTheme owns that document.
    expect(booted.dark).toBe(false);
    expect(booted.theme).toBeUndefined();
    expect(booted.radius).toBeUndefined();
    expect(booted.vars['--size-layout']).toBe('');
  });

  it.each([
    ['apply.4truck.us', true],
    ['apply.localhost', true],
    ['dash.4truck.us', false],
    // Not a prefix match: the guard is `indexOf(...) === 0`, so a host
    // that merely CONTAINS "apply." must still boot normally.
    ['not-apply.4truck.us', false],
    ['4truck.us', false],
  ])('hostname %s → stands down: %s', (hostname, shouldSkip) => {
    storeTheme({ color: 'dark-green', radius: 'pill' });
    const booted = runBootOnHost(hostname);
    expect(booted.theme, `hostname ${hostname}`)
      .toBe(shouldSkip ? undefined : 'dark-green');
  });
});

describe('theme-boot source', () => {
  const source = bootScriptSource();

  it('states every enum value the registry accepts', () => {
    for (const v of [...THEME_COLORS, ...THEME_RADII]) {
      expect(source, `boot script is missing the '${v}' branch`).toContain(`'${v}'`);
    }
  });

  it('states the registry defaults', () => {
    for (const v of Object.values(THEME_DEFAULT)) {
      expect(source, `boot script is missing the default '${v}'`).toContain(`'${v}'`);
    }
  });

  it('states the same clamp bounds as the registry', () => {
    expect(source, 'boot script clamp floor drifted from SIZE_MIN').toContain(String(SIZE_MIN));
    expect(source, 'boot script clamp ceiling drifted from SIZE_MAX').toContain(String(SIZE_MAX));
  });

  it('names every region the registry knows about', () => {
    for (const r of SIZE_REGIONS) {
      expect(source, `boot script is missing the '${r}' region`).toContain(`'${r}'`);
    }
  });

  it('reads both canonical storage keys the adapter writes', () => {
    expect(source).toContain(`'${LS_PREFIX}theme'`);
    expect(source).toContain(`'${LS_PREFIX}size'`);
  });

  it('never calls a storage writer', () => {
    expect(source).not.toMatch(/\bsetItem\b|\bremoveItem\b|\blocalStorage\.clear\b/);
  });

  it('no longer stamps the retired density attribute', () => {
    expect(source).not.toContain('data-density');
    expect(source).not.toContain('dataset.density');
  });
});
