/**
 * The material layer is three claims about the CASCADE, and a claim
 * about cost. None of them are visible by reading a component, so they
 * are asserted against the stylesheet itself.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..', '..');
const CSS = readFileSync(join(SRC, 'index.css'), 'utf8');
/** Comments blanked in place, so line numbers survive. */
const CODE = CSS.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));

describe('where the material rules sit in the cascade', () => {
  it('defines .surface outside every @layer', () => {
    // Tailwind v3 orders its output rather than emitting native cascade
    // layers, so a rule written after `@tailwind utilities` in source
    // wins a specificity tie against a utility. Inside `@layer
    // components` it would be hoisted ABOVE the utilities and `bg-card`
    // would beat it — the surface would silently stay opaque.
    const idx = CODE.indexOf('\n.surface {');
    expect(idx, '.surface is not declared at top level').toBeGreaterThan(0);
    const before = CODE.slice(0, idx);
    // Every @layer opened before this point must also have closed.
    const opens = (before.match(/@layer\s+\w+\s*\{/g) || []).length;
    const braces = (before.match(/\{/g) || []).length - (before.match(/\}/g) || []).length;
    expect(opens, 'no @layer blocks at all — has the file changed shape?').toBeGreaterThan(0);
    expect(braces, '.surface is inside an unclosed block, probably an @layer').toBe(0);
  });

  it('keeps every glass rule inside @media screen', () => {
    // The print reset restores the light palette through selector
    // specificity alone — there is no `!important` anywhere in this
    // codebase. A glass rule that applied during print would blur and
    // thin a page about to become paper.
    const glass = [...CODE.matchAll(/\[data-material="glass"\]/g)].map((m) => m.index!);
    expect(glass.length, 'no glass rules found').toBeGreaterThan(1);
    for (const at of glass) {
      const before = CODE.slice(0, at);
      const screens = (before.match(/@media\s+screen\s*\{/g) || []).length;
      const opened = (before.match(/\{/g) || []).length - (before.match(/\}/g) || []).length;
      // The escape hatch names the attribute in a selector that must
      // work everywhere, including print. It is the one exception.
      const line = CODE.slice(at, CODE.indexOf('\n', at) + 200);
      if (line.includes('surface-opaque')) continue;
      expect(screens, `a glass rule at offset ${at} is outside @media screen`).toBeGreaterThan(0);
      expect(opened, `a glass rule at offset ${at} is not nested in a block`).toBeGreaterThan(0);
    }
  });
});

describe('what the solid path costs', () => {
  const surfaceRule = /\n\.surface \{([^}]*)\}/.exec(CODE)?.[1] ?? '';

  it('puts no backdrop-filter on the base class', () => {
    // At blur 0 it would still promote all 178 cards to their own
    // compositing layer for no visual effect. The default has to cost
    // exactly nothing.
    expect(surfaceRule, '.surface rule not found').not.toBe('');
    expect(surfaceRule).not.toMatch(/backdrop-filter/);
  });

  it('puts no box-shadow on the base class either', () => {
    // This rule is unlayered and so outranks a utility. A shadow here
    // would beat the `shadow-lg` that dialogs, sheets and menus already
    // carry and silently flatten every one of them.
    expect(surfaceRule).not.toMatch(/box-shadow/);
    // It belongs to the glass rule, which is where it is measured from.
    // Matched by extracting that rule rather than by a distance window —
    // a window silently stops matching when someone adds a comment.
    const glassSurface = /:root\[data-material="glass"\]\s+\.surface\s*\{([\s\S]*?)\n  \}/.exec(CODE)?.[1];
    expect(glassSurface, 'the glass .surface rule is gone').toBeTruthy();
    expect(glassSurface!).toMatch(/box-shadow:\s*var\(--surface-shadow\)/);
    expect(glassSurface!).toMatch(/backdrop-filter/);
  });

  it('ships solid defaults that reproduce today exactly', () => {
    const root = /:root \{([\s\S]*?)\n  \}/.exec(CODE.slice(CODE.indexOf('--background')))?.[0] ?? CODE;
    for (const [name, want] of [
      ['--surface-alpha', '1'], ['--surface-blur', '0px'],
      ['--surface-saturate', '1'], ['--surface-shadow', 'none'],
    ] as const) {
      const m = new RegExp(`${name}:\\s*([^;]+);`).exec(CODE);
      expect(m, `${name} is not declared`).not.toBeNull();
      expect(m![1].trim(), `${name} default`).toBe(want);
    }
  });
});

describe('the occlusion escape hatch', () => {
  it('exists, and cancels both the translucency and the blur', () => {
    // 21 sticky and pinned elements use their fill to HIDE the content
    // scrolling underneath — the grid's frozen columns, the pivot's left
    // rail. Glass makes them see-through and the table unreadable.
    // Scoped to glass, and asserted that way. The rule was once a
    // two-selector list whose bare half did nothing — solid mode has no
    // translucency to cancel — and a mutation deleting that half was
    // invisible to every assertion here. A rule a mutation can remove
    // without breaking anything should not be in the file.
    expect(CODE, 'the escape hatch is no longer scoped to glass')
      .toMatch(/:root\[data-material="glass"\]\s+\.surface-opaque\s*\{/);
    const rule = /:root\[data-material="glass"\]\s+\.surface-opaque\s*\{([^}]*)\}/.exec(CODE)?.[1] ?? '';
    expect(rule, '.surface-opaque is gone — the pinned columns have no way out').not.toBe('');
    expect(rule).toMatch(/background-color:\s*var\(--surface-base/);
    // Both spellings, separately. `/backdrop-filter/` matches inside
    // `-webkit-backdrop-filter`, so a mutation deleting the standard
    // property passed while only the prefixed one survived — which is
    // every modern browser ignoring the reset.
    expect(rule, 'the unprefixed reset is gone').toMatch(/(^|[\s;])backdrop-filter:\s*none/);
    expect(rule, 'the -webkit- reset is gone').toMatch(/-webkit-backdrop-filter:\s*none/);
  });
});

describe('the primitives actually use it', () => {
  // Matched against the CLASS STRING, not the word. `card.tsx` says
  // "the bordered surface a page's content sits on" in its own doc
  // comment, so a search for `surface` passed even after the primitive
  // was reverted to `bg-card` — the guard was reading prose.
  const FILES = [
    ['components/ui/card.tsx', 'cva("surface '],
    ['components/ui/dialog.tsx', 'surface surface-popover'],
    ['components/ui/sheet.tsx', 'surface surface-popover'],
    ['components/ui/select.tsx', 'surface surface-popover'],
    ['components/ui/context-menu.tsx', 'surface surface-popover'],
  ] as const;

  it('reaches every centralised surface', () => {
    // These five strings are the entire reason a material is cheap:
    // they define ~387 call sites between them. A primitive that drifts
    // back to a raw `bg-card` takes its whole subtree out of the
    // material without any test noticing otherwise.
    for (const [file, needle] of FILES) {
      const src = readFileSync(join(SRC, file), 'utf8');
      expect(src, `${file} no longer uses "${needle}"`).toContain(needle);
    }
  });

  it('leaves the inverted tooltip alone', () => {
    // Tooltip paints `bg-foreground` — it is not a surface, it is the
    // ink. Pulling it into the material would make it glass over
    // whatever it points at, which is the one place that must stay
    // readable.
    const src = readFileSync(join(SRC, 'components/ui/tooltip.tsx'), 'utf8');
    expect(src).toContain('bg-foreground');
    expect(src).not.toContain('surface-popover');
  });
});
