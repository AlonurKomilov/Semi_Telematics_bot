/**
 * The pre-paint theme script in index.html has ONE job: put <html> into
 * exactly the state ThemeProvider would put it in, but before the first
 * paint.  It cannot import anything — it runs before any module — so it
 * re-states the registry's enums and defaults as literals.
 *
 * That duplication is the whole risk.  Nothing about a drifted copy is
 * loud: the app renders correctly after hydration and wrong before it,
 * for one frame, on a machine that isn't the author's.  So this suite
 * runs the REAL script (read out of index.html, not a copy) against the
 * REAL applyTheme and asserts they agree on every valid input, on
 * garbage, on nothing at all, and on the legacy key — plus the two
 * invariants the script must never break: it writes nothing, and it
 * stands down on the public apply host.
 */
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { applyTheme } from '../context/ThemeContext';
import {
  THEME_DEFAULT, THEME_COLORS, THEME_DENSITIES, THEME_RADII,
} from '../preferences/registry';
import { LS_PREFIX } from '../preferences/local';
import type { ThemeSetting } from '../preferences/registry';

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
      + 'without it the first painted frame is the default theme.',
    );
  }
  return m[1];
}

/** Run it the way the browser would, then report what <html> looks like. */
function runBoot(): { dark: boolean; theme?: string; density?: string; radius?: string } {
  // `new Function` on purpose: the point is to execute the SHIPPED
  // source, not a hand-copied approximation that can agree with the
  // test while disagreeing with the browser.
  new Function(bootScriptSource())();
  const root = document.documentElement;
  return {
    dark: root.classList.contains('dark'),
    theme: root.dataset.theme,
    density: root.dataset.density,
    radius: root.dataset.radius,
  };
}

/** What ThemeProvider produces for the same input. */
function runApply(theme: ThemeSetting) {
  applyTheme(theme);
  const root = document.documentElement;
  return {
    dark: root.classList.contains('dark'),
    theme: root.dataset.theme,
    density: root.dataset.density,
    radius: root.dataset.radius,
  };
}

function resetRoot() {
  const root = document.documentElement;
  root.classList.remove('dark');
  delete root.dataset.theme;
  delete root.dataset.density;
  delete root.dataset.radius;
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

describe('theme-boot script ↔ applyTheme', () => {
  it('agrees with applyTheme on every valid stored theme', () => {
    for (const color of THEME_COLORS) {
      for (const density of THEME_DENSITIES) {
        for (const radius of THEME_RADII) {
          const theme: ThemeSetting = { color, density, radius };

          resetRoot();
          localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify(theme));
          const booted = runBoot();

          resetRoot();
          const applied = runApply(theme);

          expect(booted, `boot script disagrees for ${JSON.stringify(theme)}`)
            .toEqual(applied);
        }
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
    ['values outside the enums', JSON.stringify({ color: 'chartreuse', density: 9, radius: null })],
  ])('falls back to the registry default for %s', (_label, raw) => {
    localStorage.setItem(`${LS_PREFIX}theme`, raw);
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(THEME_DEFAULT));
  });

  it('completes a partially stored theme field by field, like the sanitizer', () => {
    localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify({ color: 'light' }));
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply({ ...THEME_DEFAULT, color: 'light' }));
  });

  it('reads the pre-service key when the canonical one is absent', () => {
    const legacy: ThemeSetting = { color: 'light', density: 'compact', radius: 'sharp' };
    localStorage.setItem('dashboard-theme', JSON.stringify(legacy));
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(legacy));
  });

  it('prefers the canonical key over the legacy one', () => {
    localStorage.setItem('dashboard-theme', JSON.stringify(
      { color: 'light', density: 'compact', radius: 'sharp' },
    ));
    const canonical: ThemeSetting = { color: 'dark-green', density: 'comfortable', radius: 'pill' };
    localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify(canonical));
    const booted = runBoot();
    resetRoot();
    expect(booted).toEqual(runApply(canonical));
  });

  it('writes nothing — local.ts owns the copy-forward', () => {
    localStorage.setItem('dashboard-theme', JSON.stringify(
      { color: 'light', density: 'compact', radius: 'sharp' },
    ));
    const before = { ...localStorage };
    runBoot();
    expect({ ...localStorage }).toEqual(before);
    // Specifically: it must NOT copy the legacy value forward itself.
    expect(localStorage.getItem(`${LS_PREFIX}theme`)).toBeNull();
  });

  it('stands down on the ?apply preview query', () => {
    window.history.replaceState({}, '', '/?apply');
    localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify(
      { color: 'dark-green', density: 'compact', radius: 'pill' },
    ));
    const booted = runBoot();
    // Untouched: applyPublicFormTheme owns that document.
    expect(booted).toEqual({
      dark: false, theme: undefined, density: undefined, radius: undefined,
    });
  });

  // The other half of the predicate. jsdom cannot navigate to another
  // host, so the script's `location` is shadowed by a parameter of the
  // same name — legal because the script only ever reads `.hostname` and
  // `.search`, and the injection happens OUTSIDE its IIFE.
  it.each([
    ['apply.4truck.us', true],
    ['apply.localhost', true],
    ['dash.4truck.us', false],
    // Not a prefix match: the guard is `indexOf(...) === 0`, so a host
    // that merely CONTAINS "apply." must still boot normally.
    ['not-apply.4truck.us', false],
    ['4truck.us', false],
  ])('hostname %s → stands down: %s', (hostname, shouldSkip) => {
    localStorage.setItem(`${LS_PREFIX}theme`, JSON.stringify(
      { color: 'dark-green', density: 'compact', radius: 'pill' },
    ));
    new Function('location', bootScriptSource())({ hostname, search: '' });
    const root = document.documentElement;
    expect(root.dataset.theme, `hostname ${hostname}`)
      .toBe(shouldSkip ? undefined : 'dark-green');
  });
});

describe('theme-boot script source', () => {
  const source = bootScriptSource();

  it('states every enum value the registry accepts', () => {
    for (const v of [...THEME_COLORS, ...THEME_DENSITIES, ...THEME_RADII]) {
      expect(source, `boot script is missing the '${v}' branch`).toContain(`'${v}'`);
    }
  });

  it('states the registry defaults', () => {
    for (const v of Object.values(THEME_DEFAULT)) {
      expect(source, `boot script is missing the default '${v}'`).toContain(`'${v}'`);
    }
  });

  it('reads the canonical storage key the adapter writes', () => {
    expect(source).toContain(`'${LS_PREFIX}theme'`);
  });

  it('never calls a storage writer', () => {
    expect(source).not.toMatch(/\bsetItem\b|\bremoveItem\b|\blocalStorage\.clear\b/);
  });
});
