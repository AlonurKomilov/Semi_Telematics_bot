/**
 * A pointer pack answers for every cursor the app asks for, and every
 * rule survives the browser refusing its image.
 *
 * The failure this guards is not subtle to a person and is invisible to
 * a suite: `cursor: url(...) 4 2` with no trailing keyword is an
 * INVALID declaration, so the browser drops the whole rule and leaves
 * the arrow where a hand belongs. Nothing throws, nothing logs, and the
 * only symptom is that links stop feeling like links.
 *
 * The stylesheet is the subject. The kinds come from `cursor.ts`, the
 * rules are read out of `index.css`, and a pack that half-lands fails
 * on the file that paints it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { CURSOR_KINDS, CURSOR_MAX_PX } from './cursor';
import { CURSOR_PACKS, CURSOR_IDS } from './store/items/cursor';
import { assembledCss } from '../test/stylesheet';

const SRC = join(__dirname, '..');
const CSS = assembledCss().replace(/\/\*[\s\S]*?\*\//g, '');

/** Every `cursor:` declaration a pack's block makes, by kind. */
function rulesOf(pack: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const kind of CURSOR_KINDS) {
    const sel = kind === 'default'
      ? `:root\\[data-cursor="${pack}"\\]`
      : `:root\\[data-cursor="${pack}"\\] \\.cursor-${kind}`;
    const m = new RegExp(`${sel}\\s*\\{([^}]*)\\}`).exec(CSS);
    const decl = /cursor:\s*([^;]+);/.exec(m?.[1] ?? '')?.[1];
    if (decl) out[kind] = decl.replace(/\s+/g, ' ').trim();
  }
  return out;
}

const CUSTOM = CURSOR_PACKS.filter((p) => p.id !== 'system');

describe('every pack answers for every kind', () => {
  it('has packs to check, and a system one that paints nothing', () => {
    expect(CUSTOM.length, 'no custom pack — every assertion below is vacuous')
      .toBeGreaterThan(0);
    // "System" is the absence of rules, not a rule that restores them:
    // an explicit `cursor: auto` would still have to out-rank whatever
    // a later pack declares, and the OS pointer is what no rule means.
    expect(Object.keys(rulesOf('system')),
      '"system" declares cursors; it should declare none').toEqual([]);
  });

  it('and no block for a pack the list does not offer', () => {
    const orphans = [...CSS.matchAll(/\[data-cursor="([^"]+)"\]/g)]
      .map((m) => m[1]).filter((id) => !CURSOR_IDS.includes(id));
    expect([...new Set(orphans)], 'CSS paints a pack nobody can choose').toEqual([]);
  });

  it('covers all nine kinds — a pack that covers some is two pointers', () => {
    for (const p of CUSTOM) {
      const rules = rulesOf(p.id);
      const missing = CURSOR_KINDS.filter((k) => !(k in rules));
      expect(missing, `${p.id} leaves ${missing.join(', ')} to the operating system`)
        .toEqual([]);
    }
  });
});

describe('every rule survives the image being refused', () => {
  it('ends in a keyword, so an invalid image falls back instead of vanishing', () => {
    let checked = 0;
    for (const p of CUSTOM)
      for (const [kind, decl] of Object.entries(rulesOf(p.id))) {
        checked++;
        // `url(...) x y, keyword` — the part after the last comma.
        const tail = decl.slice(decl.lastIndexOf(',') + 1).trim();
        expect(tail, `${p.id}/${kind} has no fallback keyword — the rule is invalid`)
          .toMatch(/^[a-z-]+$/);
        // And the RIGHT keyword: falling back to `auto` on a link is
        // the arrow again, which is the bug this exists to prevent.
        const want = kind === 'default' ? 'auto' : kind;
        expect(tail, `${p.id}/${kind} falls back to "${tail}"`).toBe(want);
      }
    expect(checked, 'no rules parsed — nothing was checked')
      .toBeGreaterThanOrEqual(CURSOR_KINDS.length);
  });

  it('and the check can fail', () => {
    // The control: a declaration written the broken way must be caught
    // by the same slice, so a passing suite is a measuring one.
    const broken = "url('data:image/svg+xml,x') 4 2";
    expect(broken.slice(broken.lastIndexOf(',') + 1).trim()).not.toMatch(/^[a-z-]+$/);
  });
});

describe('the images are ours, inline, and small enough to appear', () => {
  it('every cursor is a data: URI — nothing is fetched', () => {
    for (const p of CUSTOM)
      for (const [kind, decl] of Object.entries(rulesOf(p.id)))
        expect(decl, `${p.id}/${kind} fetches its pointer`)
          .toMatch(/url\("data:image\/svg\+xml,/);
  });

  it('and none is larger than a browser will draw', () => {
    // Over 128px is ignored outright and Windows is unreliable past 32:
    // an oversized cursor is not a big pointer, it is NO pointer.
    for (const p of CUSTOM)
      for (const [kind, decl] of Object.entries(rulesOf(p.id))) {
        const w = +(/width='(\d+)'/.exec(decl)?.[1] ?? 0);
        const h = +(/height='(\d+)'/.exec(decl)?.[1] ?? 0);
        expect(w, `${p.id}/${kind} declares no width`).toBeGreaterThan(0);
        expect(Math.max(w, h), `${p.id}/${kind} is ${w}×${h}`)
          .toBeLessThanOrEqual(CURSOR_MAX_PX);
      }
  });

  it('and every hotspot lands inside its own image', () => {
    // A hotspot outside the bitmap is undefined behaviour: the browser
    // picks its own, and the click lands somewhere the person did not
    // point at — which reads as the app being misaligned, not as a
    // cursor being wrong.
    for (const p of CUSTOM)
      for (const [kind, decl] of Object.entries(rulesOf(p.id))) {
        const m = /\)\s+(\d+)\s+(\d+)\s*,/.exec(decl);
        expect(m, `${p.id}/${kind} names no hotspot`).not.toBeNull();
        const w = +(/width='(\d+)'/.exec(decl)?.[1] ?? 0);
        const h = +(/height='(\d+)'/.exec(decl)?.[1] ?? 0);
        expect(+m![1], `${p.id}/${kind} hotspot x is outside`).toBeLessThan(w);
        expect(+m![2], `${p.id}/${kind} hotspot y is outside`).toBeLessThan(h);
      }
  });
});

describe('the pack out-ranks the utilities it replaces', () => {
  /**
   * Tailwind writes `.cursor-pointer` at (0,1,0). A pack rule has to
   * beat that on specificity, because `!important` is reserved here for
   * the reduced-motion floor and nothing else in this stylesheet uses
   * it — a pack that reached for it would be the second.
   */
  it('by specificity, never by !important', () => {
    for (const p of CUSTOM) {
      const block = new RegExp(
        `:root\\[data-cursor="${p.id}"\\][^{]*\\{([^}]*)\\}`, 'g');
      const bodies = [...CSS.matchAll(block)].map((m) => m[1]);
      expect(bodies.length, `${p.id} has no rules`).toBeGreaterThan(0);
      for (const b of bodies)
        expect(b, `${p.id} reaches for !important`).not.toMatch(/!important/);
    }
  });

  it('and every utility the app writes is answered', () => {
    // The kinds are supposed to BE what the source uses. A utility
    // nobody covers is a cursor that stays the OS one while the rest
    // of the screen has changed.
    const used = new Set<string>();
    /** Written behind a variant. The pack cannot reach these at all. */
    const prefixed = new Set<string>();
    const walk = (d: string) => {
      for (const e of readdirSync(d)) {
        const f = join(d, e);
        if (statSync(f).isDirectory()) { walk(f); continue; }
        if (!/\.tsx?$/.test(f) || f.includes('.test.')) continue;
        const code = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
        // NOT preceded by a variant prefix. Tailwind compiles
        // `disabled:cursor-not-allowed` to a class token of that exact
        // name, which `.cursor-not-allowed` cannot match — so counting
        // it here reported a cursor as ANSWERED that the pack can never
        // reach. That was this guard certifying its own blind spot.
        for (const m of code.matchAll(/(^|[^\w:-])cursor-([a-z-]+)\b/g)) used.add(m[2]);
        for (const m of code.matchAll(/[\w-]+:cursor-([a-z-]+)\b/g)) prefixed.add(m[1]);
      }
    };
    walk(SRC);
    expect(used.size, 'no cursor utilities found — this measures nothing')
      .toBeGreaterThan(4);
    const uncovered = [...used].filter(
      (k) => !(CURSOR_KINDS as readonly string[]).includes(k));
    expect(uncovered.sort(), 'a cursor the app writes that no pack answers for')
      .toEqual([]);

    // The blind spot, stated rather than hidden. A variant-prefixed
    // utility is a real hole — the pack is unreachable there — and the
    // count of them is ratcheted in `mods/coverage.test.ts` so it lives
    // in one place. What this asserts is that the hole still EXISTS in
    // the form we think it does: if it ever empties, the ratchet there
    // is the thing to lower.
    expect(prefixed.size, 'no variant-prefixed cursors found — either they were all '
      + 'fixed (lower the pin in coverage.test.ts) or this scan stopped matching')
      .toBeGreaterThan(0);
  });

  it('answers the arrow as a CLASS, not only by inheritance', () => {
    // `:root` gives every element the arrow through inheritance, and an
    // element that declares its own cursor loses it — `cursor-default`
    // is written on thirteen elements in the product, each of them a
    // deliberate "this is not interactive", and each was falling back
    // to the OS pointer while everything around it wore the pack.
    for (const pack of CUSTOM) {
      const rules = rulesOf(pack.id);
      expect(rules.default, `${pack.id} answers the arrow only on :root`).toBeTruthy();
      expect(CSS, `${pack.id} has no .cursor-default rule`)
        .toMatch(new RegExp(`:root\\[data-cursor="${pack.id}"\\] \\.cursor-default`));
    }
  });
});
