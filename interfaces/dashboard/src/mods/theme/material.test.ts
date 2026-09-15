/**
 * The material layer is three claims about the CASCADE, and a claim
 * about cost. None of them are visible by reading a component, so they
 * are asserted against the stylesheet itself.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { assembledCss, engineCss } from '../../test/stylesheet';

const SRC = join(__dirname, '..', '..');
// Assembled: glass is a pack file now, inlined the way Vite inlines it.
const CSS = assembledCss();
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
    const glassSurface = /:root\[data-material="glass"\]\s+\.surface\s*\{([\s\S]*?)\n {2}\}/.exec(CODE)?.[1];
    expect(glassSurface, 'the glass .surface rule is gone').toBeTruthy();
    expect(glassSurface!).toMatch(/box-shadow:\s*var\(--surface-shadow\)/);
    expect(glassSurface!).toMatch(/backdrop-filter/);
  });

  it('ships solid defaults that reproduce today exactly', () => {
    // The ENGINE sheet, by name: the first declaration of each token is
    // the base, and in the assembled sheet the glass pack — imported at
    // the top — declares them first. A default read from there would be
    // glass's, and this test would be asserting the wrong material.
    const ENGINE = engineCss().replace(/\/\*[\s\S]*?\*\//g, '');
    for (const [name, want] of [
      ['--surface-alpha', '1'], ['--surface-blur', '0px'],
      ['--surface-saturate', '1'], ['--surface-shadow', 'none'],
    ] as const) {
      const m = new RegExp(`${name}:\\s*([^;]+);`).exec(ENGINE);
      expect(m, `${name} is not declared`).not.toBeNull();
      expect(m![1].trim(), `${name} default`).toBe(want);
    }
  });
});

describe('the occlusion escape hatch', () => {
  it('exists, and cancels both the translucency and the blur', () => {
    // A surface positioned out of flow uses its fill to HIDE what it
    // covers, and glass makes it see-through instead. This comment used
    // to name "21 sticky and pinned elements" that opted out; none did,
    // and the grid's frozen columns were never at risk — they paint
    // opaque utilities of their own. The population this rule serves is
    // claimed by geometry now; `.surface-opaque` is for the one case
    // geometry cannot see.
    // Scoped to glass, and asserted that way. The rule was once a
    // two-selector list whose bare half did nothing — solid mode has no
    // translucency to cancel — and a mutation deleting that half was
    // invisible to every assertion here. A rule a mutation can remove
    // without breaking anything should not be in the file.
    expect(CODE, 'the escape hatch is no longer scoped to glass')
      .toMatch(/:root\[data-material="glass"\]\s+\.surface-opaque\s*[,{]/);
    const rule = /:root\[data-material="glass"\]\s+\.surface-opaque\s*(?:,[^{]*)?\{([^}]*)\}/
      .exec(CODE)?.[1] ?? '';
    expect(rule, '.surface-opaque is gone — the pinned columns have no way out').not.toBe('');
    expect(rule).toMatch(/background-color:\s*var\(--surface-base/);
    // Both spellings, separately. `/backdrop-filter/` matches inside
    // `-webkit-backdrop-filter`, so a mutation deleting the standard
    // property passed while only the prefixed one survived — which is
    // every modern browser ignoring the reset.
    expect(rule, 'the unprefixed reset is gone').toMatch(/(^|[\s;])backdrop-filter:\s*none/);
    expect(rule, 'the -webkit- reset is gone').toMatch(/-webkit-backdrop-filter:\s*none/);
  });

  /**
   * A POPOVER TAKES IT WITHOUT ASKING.
   *
   * The comment above says "those sites opt out explicitly". Not one
   * site did: `surface-opaque` existed in the glass sheet, in a comment
   * in `index.css`, and in the assertion above. Nowhere else. So every
   * floating surface in the product was translucent, and the persona
   * menu in the top bar rendered the SIDEBAR through itself — at alpha
   * 0.72 the text behind keeps 28% of its contrast, which is legible
   * grey whether or not the blur lands.
   *
   * Occlusion is not a property a call site should have to remember,
   * and this one proves the point: twenty-four sites carry
   * `.surface-popover`, and not one of them would have thought to add a
   * second class. What defines that category IS floating over content
   * nobody chose.
   */
  it('and a popover takes it without having to ask', () => {
    const selectors = /([^{}]*)\{[^}]*backdrop-filter:\s*none[^}]*\}/.exec(CODE)?.[1] ?? '';
    expect(
      selectors,
      'a popover is translucent again. Every menu, select, context menu, dialog, '
        + 'sheet and banner floats over content nobody chose, so it must occlude — '
        + 'and no call site should have to remember that.',
    ).toMatch(/\.surface\.surface-popover/);
  });

  /**
   * AND THE CATEGORY IS GEOMETRY, not a class somebody remembered.
   *
   * `.surface-popover` was the first answer and it did not reach the
   * reported bug at all: the persona menu is a `<Card>`, and
   * `cardVariants` is `"surface border border-border rounded-lg"` — no
   * popover class, and no call site would have thought to add one.
   *
   * What these surfaces share is not a name. They are positioned out of
   * flow, over content nobody chose, and the product writes that in
   * bare Tailwind tokens only — so three selectors claim thirteen sites
   * with no call-site edits and nothing left to forget.
   */
  it('and a surface positioned out of flow occludes, whatever it is called', () => {
    const selectors = /([^{}]*)\{[^}]*backdrop-filter:\s*none[^}]*\}/.exec(CODE)?.[1] ?? '';
    for (const pos of ['absolute', 'fixed', 'sticky']) {
      expect(
        selectors,
        `a \`.surface.${pos}\` is translucent again. It floats over content nobody `
          + 'chose, so it must occlude — and the class that says so is the one the '
          + 'author already wrote for layout.',
      ).toMatch(new RegExp(`\\.surface\\.${pos}`));
    }
  });

  /**
   * `.surface.surface-popover`, never `.surface-popover` alone.
   *
   * The glass rung is `:root[data-material="glass"] .surface` at
   * (0,2,0). A single-class override ties it, and a tie is decided by
   * which line came last — a rule this sheet already learned about
   * vendor stylesheets, where the answer differed between `vite dev`
   * and the production build.
   */
  it('and it out-specifies the rung it overrides rather than tying it', () => {
    expect(CODE, 'the popover override ties the glass rung instead of beating it')
      .not.toMatch(/:root\[data-material="glass"\]\s+\.surface-popover\s*[,{]/);
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

describe('glass does not switch the shader off', () => {
  const GLASS = readFileSync(
    join(__dirname, '..', 'store', 'items', 'material', 'glass.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  it('every shadow it declares answers the light', () => {
    // The rule that applies this shadow out-specifies the light rung in
    // index.css — deliberately, so it can beat the `shadow-lg` a dialog
    // carries. That meant a literal here silently DISABLED the Shaders
    // axis on every card, dialog, sheet, select and context-menu, and
    // neither axis's own tests could see it: material checked its rule
    // existed, shaders checked its tokens existed, and nothing checked
    // that one read the other.
    const decls = [...GLASS.matchAll(/--surface-shadow:\s*([^;]+);/g)].map((m) => m[1]);
    expect(decls.length, 'glass declares no shadow — this checks nothing')
      .toBeGreaterThan(1);
    for (const d of decls) {
      expect(d, `a --surface-shadow with no light term: ${d.slice(0, 60)}`)
        .toMatch(/var\(--light-/);
    }
  });

  it('and the scan can fail', () => {
    expect('0 1px 2px rgb(0 0 0 / 6%)').not.toMatch(/var\(--light-/);
    expect('0 calc(1px * var(--light-lift, 1)) 2px').toMatch(/var\(--light-/);
  });
});

describe('the axis reaches every surface that is one', () => {
  /**
   * `.surface` is the class the material axis paints through — nothing
   * else is reached. A surface that writes `bg-popover` by hand keeps
   * its colour and loses the axis: pick glass, and it stays solid while
   * every dialog and card around it goes translucent.
   *
   * Twenty sites did exactly that, across eleven files, which is most of
   * what "material only covers a fifth of the product" meant.
   */
  const OCCLUDERS: Record<string, string> = {
    'components/ui/select.tsx':
      'the scroll arrows inside an already-surfaced popup. They paint the '
      + 'popover colour to OCCLUDE the list scrolling under them — the same '
      + 'reason glass.css keeps an escape hatch for sticky and pinned '
      + 'elements. A translucent occluder occludes nothing.',
  };

  const tsxFiles = (dir: string, out: string[] = []): string[] => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== 'node_modules') tsxFiles(full, out);
      } else if (name.endsWith('.tsx') && !name.includes('.test.')) out.push(full);
    }
    return out;
  };

  it('nothing paints the popover colour outside the axis', () => {
    const SRC = join(__dirname, '..', '..');
    const offenders: string[] = [];
    let exempt = 0;
    for (const full of tsxFiles(SRC)) {
      const rel = relative(SRC, full).split(sep).join('/');
      const src = readFileSync(full, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      for (const m of src.matchAll(/className=(?:"([^"]*)"|\{([\s\S]{0,600}?)\}\s*(?:\n|\/?>|[a-zA-Z-]+=))/g)) {
        const body = m[2] ?? '';
        const c = m[1] ?? [...body.matchAll(/'([^']*)'|"([^"]*)"|`([^`]*)`/g)]
          .map((s) => s[1] ?? s[2] ?? s[3] ?? '').join(' ');
        if (!/\bbg-popover(?![/\w-])/.test(c)) continue;
        if (OCCLUDERS[rel]) { exempt += 1; continue; }
        if (/\bsurface\b/.test(c)) continue;
        offenders.push(`${rel}:${src.slice(0, m.index ?? 0).split('\n').length}`);
      }
    }
    expect(exempt, 'the occluder exemption names a file that no longer paints it')
      .toBeGreaterThan(0);
    expect(offenders,
      'a surface paints the popover colour and never joins the material axis — '
      + 'use `surface surface-popover` instead of `bg-popover`')
      .toEqual([]);
  });
});


/**
 * The two things that made the menu unreadable, neither of them the
 * translucency itself.
 */
describe('a surface paints its own colour, on its own backdrop', () => {
  const CSS = readFileSync(join(SRC, 'index.css'), 'utf8');

  /**
   * `--surface-base` is a CUSTOM PROPERTY, and those inherit — so a
   * `<Card>` mounted inside the sidebar painted in the RAIL's colour.
   * The persona menu is four levels down from `.surface-sidebar`; in
   * dark that made it the same lightness as the thing it covers.
   * Opaque and invisible is not better than translucent and illegible.
   *
   * Stated on the CARD, not registered with `@property
   * { inherits: false }` — which was the first fix and reached too far:
   * it changes the property's behaviour for every element in the
   * product at once, including ones nobody has written yet, to solve a
   * problem that belongs to one primitive.
   */
  it('a Card declares its own base rather than inheriting one', () => {
    expect(CSS, '`.surface-card` is gone — a Card inside the sidebar will paint in '
      + 'the rail colour instead of its own.')
      .toMatch(/\.surface-card\s*\{[^}]*--surface-base:\s*var\(--card\)/);
    const card = readFileSync(join(SRC, 'components/ui/card.tsx'), 'utf8');
    expect(card, 'the Card primitive stopped declaring its base')
      .toMatch(/cva\("surface surface-card/);
  });

  /**
   * `backdrop-filter` on an ANCESTOR makes a descendant's own filter a
   * no-op — which is why the menu showed the sidebar through itself
   * CRISP rather than smeared. The shell's content envelope wraps the
   * header and every page, and its only visible pixels are the 8px
   * gutter: carrying `.surface` bought a frosted frame and cost a
   * viewport-sized backdrop root over every card in the app.
   */
  it('and the shell envelope is not one giant backdrop root', () => {
    const shell = readFileSync(join(SRC, 'shells/AppShell.tsx'), 'utf8');
    const envelope = /className="flex-1 flex flex-col overflow-hidden[^"]*"/.exec(shell)?.[0] ?? '';
    expect(envelope, 'the content envelope moved — this reader is stale').not.toBe('');
    expect(
      /\bsurface\b/.test(envelope.replace(/surface-\w+/g, '')),
      'the shell envelope carries `.surface` again. Under Glass that is a '
        + 'viewport-sized backdrop root, and every card inside it loses its own blur '
        + 'silently — for an 8px frosted gutter.',
    ).toBe(false);
  });
});
