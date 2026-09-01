/**
 * Mods is the umbrella; theme is the COLOUR part inside it.
 *
 * That is the owner's model, and the code kept saying the reverse:
 * `THEME_MODS` held the mod list, `ThemeMod` was a whole look, and
 * `ThemeMaterial`/`ThemeMotion`/`ThemeIcons` named three axes that have
 * nothing to do with colour. The proof it confused people was already in
 * the tree — `shells/AppShell.tsx` read `theme.entrance`, a page
 * animation flag, out of a hook called `useTheme`.
 *
 * Renaming them once fixes today. This fixes tomorrow: the barrel may
 * only export a theme-flavoured name for something that really is
 * colour, and the list of those is written down. A new `ThemeSomething`
 * fails here until someone either renames it or adds it with a reason.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const barrel = readFileSync(join(SRC, 'mods/index.ts'), 'utf8');

/**
 * Theme-flavoured names the barrel is allowed to export, each because it
 * genuinely means COLOUR. Anything else with the prefix is the inversion
 * this file exists to catch.
 */
const COLOUR_EXPORTS: Record<string, string> = {
  THEME_PACKS: 'the colour packs — a pack IS a hue and its derived tokens',
  ThemePack: 'the type of the above',
  applyTheme: 'maps the axes onto <html>; the pre-paint script mirrors it byte for byte',
  Theme: 'deprecated alias for the stored shape — see context.tsx',
  ColorTheme: 'deprecated alias for the pre-split mode+accent pair',
  ThemeSeed: 'the palette seed — a mode plus two hex colours, nothing else',
};

/** Every identifier the barrel re-exports, type-only ones included. */
function barrelExports(): string[] {
  const out: string[] = [];
  for (const block of barrel.matchAll(/export\s*(?:type\s*)?\{([^}]*)\}/g)) {
    for (const raw of block[1].split(',')) {
      const name = raw.replace(/\btype\b/, '').split(/\s+as\s+/).pop()?.trim();
      if (name) out.push(name);
    }
  }
  return out;
}

describe('the barrel says mods, not theme', () => {
  it('finds exports at all — a regex that matched nothing would pass everything', () => {
    expect(barrelExports().length).toBeGreaterThan(30);
  });

  it('exports a theme-flavoured name only for something that is colour', () => {
    const offenders = barrelExports().filter(
      (n) => /^(Theme|THEME_)/.test(n) && !(n in COLOUR_EXPORTS),
    );
    expect(
      offenders,
      `${offenders.join(', ')} — mods is the umbrella and theme is the colour part inside it. `
      + 'Rename to the Mod* / MOD_* vocabulary, or add it to COLOUR_EXPORTS with the reason it '
      + 'really is colour.',
    ).toEqual([]);
  });

  it('keeps every allowlisted colour name real', () => {
    // An allowlist nobody prunes becomes a list of names that no longer
    // exist, and then it is not an allowlist, it is a comment.
    const all = barrelExports();
    for (const name of Object.keys(COLOUR_EXPORTS)) {
      expect(all, `${name} is allowlisted but the barrel no longer exports it`).toContain(name);
    }
  });
});

describe('one name, one declaration', () => {
  // These were declared twice — mods/catalogue.ts and preferences/registry.ts
  // both derived them off the same arrays — so the same type name resolved
  // through two declarations depending on which barrel you imported from.
  // Identical today, and invisible until one array changes.
  const ONCE = ['ModMaterial', 'ModMotion', 'ModIcons'];

  it.each(ONCE)('%s is declared exactly once', (name) => {
    const files = [
      'mods/catalogue.ts',
      'preferences/registry.ts',
      'mods/context.tsx',
    ];
    const declarations = files.filter((f) =>
      new RegExp(`export\\s+type\\s+${name}\\s*=`).test(readFileSync(join(SRC, f), 'utf8')));
    expect(
      declarations,
      `${name} is declared in ${declarations.join(' and ') || 'nowhere'} — it must be declared `
      + 'in mods/catalogue.ts (which owns the array) and re-exported everywhere else',
    ).toEqual(['mods/catalogue.ts']);
  });
});
