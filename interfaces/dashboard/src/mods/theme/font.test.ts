/**
 * The typeface axis, and the two copies of it that must agree.
 *
 * A font pack exists in three places by necessity: a list in the
 * catalogue (so the panel can offer it), a block in `index.css` (so the
 * browser can apply it), and a preview stack in `ModPanel` (so the chip
 * can be drawn in the face it names). Only the first is importable — the
 * other two are a stylesheet and a render-time style — so the drift is
 * caught here rather than prevented by the type system.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { FONT_PACKS, MOD_FONTS } from '../catalogue';
import { MOD_DEFAULT } from '../../preferences';

const CSS = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');
const PANEL = readFileSync(join(__dirname, '..', 'panel', 'Interface.tsx'), 'utf8');
const BOOT = readFileSync(join(__dirname, '..', '..', '..', 'index.html'), 'utf8');

/** The stack a `[data-font]` block declares, or null. */
const blockFor = (id: string): string | null => {
  const m = new RegExp(
    `\\[data-font="${id}"\\]\\s*\\{[^}]*--font-sans:\\s*([^;]+);`,
  ).exec(CSS);
  return m ? m[1].replace(/\s+/g, ' ').trim() : null;
};

describe('the packs the panel offers are packs the browser can apply', () => {
  it('finds more than one, or there is no axis', () => {
    expect(FONT_PACKS.length).toBeGreaterThan(2);
    expect(MOD_FONTS).toEqual(FONT_PACKS.map((f) => f.id));
  });

  it('the default has no block, because it IS the base', () => {
    // Same shape as blue among the accents: an unstamped document must
    // paint what it always painted, so the default is the bare :root
    // declaration and gets no attribute block of its own.
    expect(MOD_DEFAULT.font).toBe('geist');
    expect(blockFor('geist'), 'the default grew a block — an unstamped page now differs').toBeNull();
    expect(CSS, ':root declares no --font-sans').toMatch(/--font-sans:\s*'Geist Variable'/);
  });

  it('every other pack declares a stack', () => {
    const missing = FONT_PACKS
      .filter((f) => f.id !== MOD_DEFAULT.font && blockFor(f.id) === null)
      .map((f) => f.id);
    expect(missing, 'a pack the panel offers that the stylesheet cannot paint').toEqual([]);
  });

  it('every stack ends in a generic family, so it always resolves', () => {
    const GENERIC = /(sans-serif|serif|monospace|cursive|system-ui)\s*$/;
    for (const f of FONT_PACKS) {
      const stack = blockFor(f.id);
      if (!stack) continue;
      expect(GENERIC.test(stack), `${f.id} falls back to nothing: ${stack}`).toBe(true);
    }
  });

  it('the chip previews match the stacks the stylesheet ships', () => {
    // The chips are drawn in the face they name; a preview that drifted
    // from the block would show one thing and apply another.
    const norm = (v: string) => v.replace(/\s+/g, ' ').replace(/"/g, "'").trim();
    for (const f of FONT_PACKS) {
      const stack = blockFor(f.id);
      if (!stack) continue;
      const m = new RegExp(`${f.id}:\\s*("[^"]+"|'[^']+')`).exec(PANEL);
      expect(m, `${f.id} has no preview stack in ModPanel`).not.toBeNull();
      expect(norm(m![1].slice(1, -1)), `${f.id}: the chip is drawn in a different face than it applies`)
        .toBe(norm(stack));
    }
  });
});

describe('the pre-paint script knows the axis', () => {
  it('reads and stamps it — a font applied late reflows every word', () => {
    expect(BOOT, 'the boot script never reads the font').toMatch(/t\.font/);
    expect(BOOT, 'the boot script never stamps data-font').toMatch(/dataset\.font\s*=/);
  });

  it('and knows the same list the catalogue does', () => {
    // Restated in the boot script because there is no module system
    // before React — so it is checked, not trusted.
    for (const id of MOD_FONTS)
      expect(BOOT, `the boot script would refuse the '${id}' pack and fall back`)
        .toContain(`'${id}'`);
  });
});
