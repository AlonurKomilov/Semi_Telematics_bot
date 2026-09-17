/**
 * THE FRAME IS ONE OBJECT SEEN FROM FOUR DIRECTIONS.
 *
 * The rail, the header and the two gutters are the chrome the page card
 * sits in. Nothing in the markup says they belong together, so they
 * drifted: the left and the top were elements carrying the full class
 * list, and the right and the bottom were `pr-2 pb-2` — eight pixels of
 * PADDING on the envelope around them. Padding cannot take a border, a
 * rim, a radius or a lens, so two of the four sides were unreachable by
 * any material, and a mods change landed on half a frame. The owner
 * found it by using the product: "sometimes only the top and the left
 * take effect, sometimes only the left".
 *
 * These guards are what make "one change, four sides" a fact rather
 * than an intention. They are deliberately DERIVED — the class list is
 * read out of the shell rather than written here, because a guard
 * holding its own copy of the thing it guards is not a guard.
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { assembledCss } from '../test/stylesheet';

const SRC = join(__dirname, '..');
const read = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

/** Every `className` value in a file, whichever of the three
 *  delimiters it uses. Borrowed in shape from `wallpaper.test.ts`,
 *  which learned the hard way that stopping at the first quote reads a
 *  template literal as two values and finds neither. */
const CLASSNAME = new RegExp(
  ['className="[^"]*"', 'className=\\{`[^`]*`\\}', "className=\\{'[^']*'\\}"].join('|'),
  'g',
);
/** `bg-sidebar`, not `bg-sidebar-border`. */
const CHROME_FILL = /\bbg-sidebar(?![\w-])/;

/** The sides of the frame, found by what they PAINT rather than by a
 *  list of files — a fifth one added anywhere gets measured too. */
const sides = (): { where: string; cls: string }[] => {
  const out: { where: string; cls: string }[] = [];
  for (const rel of ['shells/AppShell.tsx', 'components/Sidebar.tsx'])
    for (const m of read(rel).match(CLASSNAME) ?? [])
      if (CHROME_FILL.test(m)) out.push({ where: rel, cls: m });
  return out;
};

describe('the sides of the frame are one object', () => {
  it('there are five of them, and each one is a decision', () => {
    // The rail, the header, the bottom, and TWO vertical gutters: the
    // one that ends the page — which becomes the split between the two
    // pages when the assistant opens — and the one that ends the
    // sub-page beside it. Not a taste: fewer means a side has gone back
    // to being padding or a margin, which is how both of the bugs in
    // this file's docstring started, and more means something else
    // began painting the chrome without anyone deciding it is frame.
    expect(sides().map((s) => s.where)).toHaveLength(5);
  });

  it('and every one carries the same markers', () => {
    const MARKERS = ['surface', 'surface-sidebar', 'chrome-pane'];
    for (const { where, cls } of sides())
      for (const marker of MARKERS)
        expect(cls, `${where}: a side of the frame is missing \`${marker}\`, so a rule `
          + 'written for the frame reaches the others and not this one')
          .toMatch(new RegExp(`\\b${marker}(?![\\w-])`));
  });

  it('and the envelope around them paints nothing', () => {
    // The layout box that holds the header, the page and the gutters.
    // It is every card's ancestor, and `.surface` on an ancestor makes
    // a descendant's own backdrop filter a no-op — the persona menu
    // showed the sidebar through itself CRISP for exactly that reason.
    // So it may not paint chrome, and the gutters exist precisely so it
    // does not have to.
    const envelope = /className="flex-1 flex flex-col overflow-hidden"/;
    expect(read('shells/AppShell.tsx'), 'the content envelope is gone or started painting again')
      .toMatch(envelope);
  });

  it('and no side is padding any more', () => {
    // The shape of the original bug, pinned so it cannot come back as
    // a "simplification": the envelope wore the gutters as padding.
    const src = read('shells/AppShell.tsx');
    expect(src.match(/className="[^"]*\bp[rb]-2\b[^"]*\bchrome-pane\b[^"]*"/g) ?? [],
      'a chrome surface is drawing a side of the frame with padding again')
      .toEqual([]);
  });
});

/**
 * TWO AXES, ONE PROPERTY.
 *
 * Material and Wallpaper both write `background-color` on a chrome
 * pane, both unlayered, and for as long as both have existed the
 * material won on specificity — so choosing Glass turned the frame
 * wallpaper off entirely. Nobody saw it because the reasoning lived in
 * a comment ("with a pattern on it is `background-color: transparent`")
 * and the rule that reasoning depended on had never been written.
 *
 * So this measures the RESOLVED colour through the real stylesheet
 * rather than reading a selector. A selector test would have passed on
 * the broken version too: both rules were present and correct in
 * isolation; what was wrong was which one reached the element.
 */
describe('a material may not switch the wallpaper off', () => {
  const mount = (material: string, wallpaper: string) => {
    document.head.innerHTML = '';
    document.body.innerHTML = '';
    const style = document.createElement('style');
    style.textContent = assembledCss();
    document.head.appendChild(style);
    const root = document.documentElement;
    root.setAttribute('data-material', material);
    root.setAttribute('data-wallpaper', wallpaper);
    const el = document.createElement('div');
    el.className = 'surface surface-sidebar chrome-pane';
    document.body.appendChild(el);
    return getComputedStyle(el).backgroundColor;
  };

  const transparent = (c: string) => c === 'transparent' || /,\s*0\)$/.test(c);

  it('finds the rules it is measuring', () => {
    // Without this the two cases below could both be reading an empty
    // stylesheet and agreeing about nothing.
    const css = assembledCss();
    expect(css).toMatch(/:root:not\(\[data-wallpaper="none"\]\)\s*\.chrome-pane/);
    expect(css).toMatch(/\[data-material="glass"\][^{]*\.surface\.chrome-pane/);
  });

  it('and casts no shadow onto the side next to it', () => {
    // The owner found this one by using the product: "the joins between
    // the sides still have lines and shadows, and only under glass".
    // A drop shadow is paint OUTSIDE the element's box and the sides of
    // the frame are edge-to-edge, so every side was casting onto its
    // neighbour and each seam of one continuous plate read as a
    // boundary between two objects. Under a wallpaper it fell across
    // the pattern, which has no seam at all.
    document.head.innerHTML = '';
    document.body.innerHTML = '';
    const style = document.createElement('style');
    style.textContent = assembledCss();
    document.head.appendChild(style);
    document.documentElement.setAttribute('data-material', 'glass');
    document.documentElement.setAttribute('data-wallpaper', 'none');
    const shadowOf = (cls: string) => {
      const el = document.createElement('div');
      el.className = cls;
      document.body.appendChild(el);
      return getComputedStyle(el).boxShadow;
    };
    expect(shadowOf('surface chrome-pane'),
      'a side of the frame casts a shadow, and the only thing next to it is another side')
      .toBe('none');
    // The control. Glass did not stop having shadows — a CARD is a pane
    // sitting on the ground and still throws one, which is the whole
    // difference between the two.
    expect(shadowOf('surface'), 'no surface has a shadow at all — this is measuring nothing')
      .not.toBe('none');
  });

  it('glass still lets a frame pattern through', () => {
    expect(transparent(mount('glass', 'grid')),
      'the frame wallpaper is invisible under glass — a material out-specified it')
      .toBe(true);
  });

  it('and still paints the chrome when there is no pattern', () => {
    // The control. Without it "always transparent" passes the test
    // above, and the rail would simply disappear.
    expect(transparent(mount('glass', 'none')),
      'the chrome went transparent with no pattern behind it — the rail has vanished')
      .toBe(false);
  });
});

/**
 * THE SECOND PAGE IS A PAGE.
 *
 * The assistant sits beside the first one in the shell's content row.
 * Two things about it are load-bearing and neither is visible in a
 * screenshot, because both fail by making it look like something else
 * that is also plausible:
 *
 * It must not be a `.surface`. Under Glass the rule that makes menus
 * occlude what they float over is `.surface` plus a positioned class —
 * `.fixed`, `.absolute`, `.sticky`, `.surface-popover`. The dock used
 * to be `fixed .surface`, so it matched, and it was made of a different
 * material than the rail two hundred pixels to its left while both wore
 * the same colour.
 *
 * And it must be a `page-ground`. A pattern reaches a page through that
 * class and nothing else; the dock once mirrored every other thing
 * about `<main>` except this one, and sat as a flat slab while the page
 * behind it wore the wallpaper.
 */
describe('the assistant is the second page, not a panel over the first', () => {
  const src = read('features/ai/AssistantPanel.tsx');

  it('paints no chrome and claims no surface', () => {
    for (const cls of src.match(CLASSNAME) ?? []) {
      expect(cls, 'the sub-page claims `.surface` — under glass that plus a '
        + 'positioned class is the rule that makes a menu occlude, and it would '
        + 'stop being made of the same thing as the page beside it')
        .not.toMatch(/\bsurface(?![\w-])/);
      expect(cls, 'the sub-page paints the chrome colour — it is a page, and the '
        + 'frame around it is what wears that')
        .not.toMatch(CHROME_FILL);
    }
  });

  it('and wears the ground a page wears', () => {
    expect(src, 'the sub-page lost `page-ground`, so a page wallpaper stops at its edge')
      .toMatch(/\bpage-ground\b/);
  });

  it('and is laid out by the row rather than pinned to the viewport', () => {
    // `fixed` is what put it outside the frame in the first place: it
    // covered the frame's right side instead of sitting inside it, and
    // the space beside it was a margin rather than an object.
    expect(src.match(/className=\{`[^`]*\bfixed\b[^`]*`\}|className="[^"]*\bfixed\b[^"]*"/g) ?? [],
      'the sub-page is pinned to the viewport again')
      .toEqual([]);
  });

  it('and shows its part of the same field the page shows', () => {
    // THE STRUCTURAL FORM OF A BUG THE OWNER FOUND BY DRAGGING. Every
    // ground used to start its own copy of the pattern at its own
    // top-left corner, so the two pages were out of phase across the
    // frame between them, and the sub-page's copy SLID on every drag
    // because its left edge is the one the divider moves. It was
    // repaired locally once, by anchoring that one element to its right
    // edge; anchoring every ground to the VIEWPORT instead makes the
    // repair unnecessary and fixes the phase as well — the positioning
    // area stops belonging to the element, so resizing moves nothing
    // and the grounds show their parts of one field.
    document.head.innerHTML = '';
    document.body.innerHTML = '';
    const style = document.createElement('style');
    style.textContent = assembledCss();
    document.head.appendChild(style);
    const attachmentOf = (cls: string) => {
      const el = document.createElement('div');
      el.className = cls;
      document.body.appendChild(el);
      return getComputedStyle(el).backgroundAttachment;
    };
    for (const g of ['desk-ground', 'chrome-ground', 'page-ground'])
      expect(attachmentOf(g), `${g} anchors its pattern to itself again — it will `
        + 'start its own copy, out of phase with the grounds beside it, and slide when '
        + 'the element resizes').toBe('fixed');
    // The control: `fixed` has to be something this stylesheet does on
    // purpose, not the default the environment reports for everything.
    expect(attachmentOf('not-a-ground'), 'every element reports fixed — measuring nothing')
      .not.toBe('fixed');
  });

  it('and the shell reads one answer for whether it is there', () => {
    // The gutter that closes the frame beside the sub-page and the
    // sub-page itself must agree, or the frame closes around nothing.
    // `open` is not that answer — the panel also refuses to render on
    // /ai and without the permission.
    const shell = read('shells/AppShell.tsx');
    expect(shell, 'the shell decides on its own whether the sub-page is there')
      .toMatch(/useAssistantDock\(\)/);
    expect(shell.match(/dock\.docked/g) ?? [], 'the frame does not follow the sub-page')
      .not.toEqual([]);
  });
});

/**
 * THREE PLANES, AND THE ROOT IS THE BOTTOM ONE.
 *
 * The shell root used to be the bottom of the window AND the frame's
 * ground at the same time. One element, two jobs — the same shape as
 * the gutters that were padding and the split that was a margin, and
 * the same consequence: a thing with two jobs can only be given one of
 * them. Two regional grounds cannot express one pattern running
 * continuously across the whole window at one size, because the frame
 * paints from its own origin and the page from its own.
 *
 * Separating them is what makes that expressible. The desk has no
 * pattern axis yet and the guard does not pretend otherwise — what it
 * holds is that the PLANE is its own, because that is the part that was
 * wrong and the part a later edit would quietly re-merge.
 */
describe('the desk, the frame and the page are three planes', () => {
  const shell = read('shells/AppShell.tsx');

  it('the root is the desk, and no longer the frame', () => {
    const root = (shell.match(CLASSNAME) ?? [])[0] ?? '';
    expect(root, 'the shell root is not the desk plane').toMatch(/\bdesk-ground\b/);
    expect(root, 'the root took the frame ground back — one element, two jobs again')
      .not.toMatch(/\bchrome-ground\b/);
  });

  it('and the frame has exactly one plane of its own', () => {
    const planes = (shell.match(CLASSNAME) ?? []).filter((c) => /\bchrome-ground\b/.test(c));
    expect(planes, 'the frame has no plane, or more than one').toHaveLength(1);
  });

  it('and the three name three different grounds', () => {
    const css = assembledCss();
    const groundOfClass = (cls: string) =>
      new RegExp(`\\.${cls}\\s*\\{[^}]*--ground:\\s*([^;]+);`).exec(css)?.[1].trim();
    const desk = groundOfClass('desk-ground');
    const frame = groundOfClass('chrome-ground');
    const page = groundOfClass('page-ground');
    for (const [name, v] of [['desk', desk], ['frame', frame], ['page', page]] as const)
      expect(v, `the ${name} plane declares no ground colour`).toBeTruthy();
    expect(frame, 'the frame stopped wearing the chrome colour').not.toBe(desk);
  });

  it('and a frame pane still reads the frame ground it sits on', () => {
    // THE REGRESSION THIS MOVE COULD HAVE CAUSED, measured rather than
    // argued. Glass tints a pane by `var(--ground)` — the honest half
    // of what Apple's material does by sampling: ours cannot look at
    // what is behind it, it is TOLD. That telling is INHERITANCE, so
    // moving the frame's ground off the root would have silently left
    // every frame pane reading the window's colour instead of the
    // rail's, and a pane over the rail would have stopped differing
    // from a pane over the page. The wrapper is what keeps it; a plane
    // at `inset-0` would not have.
    document.head.innerHTML = '';
    document.body.innerHTML = '';
    const style = document.createElement('style');
    style.textContent = assembledCss();
    document.head.appendChild(style);
    document.body.innerHTML =
      '<div class="desk-ground">'
      + '<div class="chrome-ground"><i id="pane" class="surface chrome-pane"></i></div>'
      + '<i id="ondesk" class="surface"></i>'
      + '</div>';
    const groundAt = (id: string) =>
      getComputedStyle(document.getElementById(id)!).getPropertyValue('--ground').trim();
    expect(groundAt('pane'), 'a frame pane no longer inherits the frame ground')
      .toBe('var(--sidebar)');
    // The control: the two planes must actually differ, or the
    // assertion above passes on a stylesheet that says one thing twice.
    expect(groundAt('ondesk'), 'the desk and the frame resolve to the same ground')
      .not.toBe(groundAt('pane'));
  });
});

/**
 * NOTHING THAT FLOATS MAY LIVE INSIDE THE FRAME.
 *
 * This is the rule the frame's material depends on, and it is invisible
 * in every other way. An element carrying a `backdrop-filter` becomes a
 * BACKDROP ROOT: a descendant's own backdrop-filter then sees only what
 * is painted inside that root, which is nothing. So a menu rendered as
 * an `absolute` child of the rail or the header cannot occlude once the
 * frame is glass — it shows the page straight through itself, crisply,
 * which is exactly how this was reported the first time.
 *
 * Both offenders are portalled now and the frame is free. What this
 * holds is that they stay that way, because the failure is silent:
 * nothing breaks until the day someone gives the frame a filter, and
 * then it breaks somewhere else entirely.
 *
 * The list is derived, not typed: the components the frame RENDERS are
 * read out of the shell and the rail, so one added tomorrow is measured
 * without anybody remembering this file exists.
 */
describe('the frame holds nothing that floats', () => {
  /** Every component the rail and the header render, by file. */
  const inhabitants = (): string[] => {
    const out = new Set<string>();
    for (const host of ['shells/AppShell.tsx', 'components/Sidebar.tsx']) {
      const src = read(host);
      for (const m of src.matchAll(/<([A-Z][A-Za-z0-9]*)\b/g)) {
        const tag = m[1];
        // Resolve the tag to the file it is imported from — a relative
        // import inside this app, never a package.
        const imp = new RegExp(`import\\s+(?:\\{[^}]*\\b${tag}\\b[^}]*\\}|${tag})\\s+from\\s+'([./@][^']*)'`)
          .exec(src)?.[1];
        if (!imp) continue;
        const rel = imp.replace(/^@\//, '').replace(/^\.\.\//, '').replace(/^\.\//, host.includes('/') ? `${host.split('/')[0]}/` : '');
        for (const ext of ['.tsx', '.ts'])
          if (existsSync(join(SRC, rel + ext))) out.add(rel + ext);
      }
    }
    return [...out];
  };

  it('finds the components the frame renders', () => {
    // Without this the sweep below can pass by resolving nothing.
    const found = inhabitants();
    expect(found.length, 'no component of the frame was resolved — this measures nothing')
      .toBeGreaterThan(5);
    expect(found, 'the account menu is not among them').toContain('components/AvatarMenu.tsx');
    expect(found, 'the persona selector is not among them').toContain('components/PersonaSelector.tsx');
  });

  /**
   * A `<Card>` IS A SURFACE AND NEVER SAYS SO.
   *
   * `cardVariants` starts `"surface surface-card border …"`, so the
   * word arrives from inside the component and no call site writes it.
   * The first version of this guard matched the word in a className and
   * reported ZERO on the persona selector — the one file it was written
   * for, whose menu is an `absolute` `<Card>`. It was green while the
   * thing it guards sat two lines away, which is the same failure
   * `glass.css` already records: `.surface-popover` was the first
   * answer and did not reach the reported bug either.
   *
   * So the tag counts as well as the word. Its extent is found by
   * walking braces and quotes rather than to the first `>`: a `<Card
   * render={<ul />}>` carries a `>` inside an attribute, and every
   * matcher in this repo that stopped there has been wrong about it
   * once.
   */
  const cardClassNames = (src: string): string[] => {
    const out: string[] = [];
    for (const m of src.matchAll(/<Card\b/g)) {
      let i = m.index! + 5, depth = 0, quote = '';
      for (; i < src.length; i++) {
        const c = src[i];
        if (quote) { if (c === quote) quote = ''; continue; }
        if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
        if (c === '{') depth++;
        else if (c === '}') depth--;
        else if (c === '>' && depth === 0) break;
      }
      const tag = src.slice(m.index!, i);
      for (const c of tag.match(CLASSNAME) ?? []) out.push(c);
    }
    return out;
  };

  /**
   * WHAT IS INSIDE A `<Dropdown>` IS NOT INSIDE THE FRAME.
   *
   * The primitive portals its panel to `<body>`, so a positioned
   * surface among its children — the tier flyout hanging off a role
   * row at `left-full` — is nobody's descendant at runtime whatever the
   * source looks like. Computed rather than exempted by name: a list of
   * blessed files stops being true the moment one of them changes, and
   * the reason it was blessed is not written down anywhere the next
   * edit will look.
   */
  const portalledSpans = (src: string): [number, number][] => {
    const spans: [number, number][] = [];
    for (const m of src.matchAll(/<Dropdown\b/g)) {
      const end = src.indexOf('</Dropdown>', m.index!);
      if (end !== -1) spans.push([m.index!, end]);
    }
    return spans;
  };

  it('and none of them pins a surface inside it', () => {
    for (const rel of inhabitants()) {
      const src = read(rel);
      const spans = portalledSpans(src);
      const inPortal = (cls: string) => {
        const at = src.indexOf(cls);
        return at !== -1 && spans.some(([a, b]) => at > a && at < b);
      };
      const surfaces = [
        ...(src.match(CLASSNAME) ?? []).filter((c) => /\bsurface(?![\w-])/.test(c)),
        ...cardClassNames(src),
      ].filter((c) => !inPortal(c));
      for (const cls of surfaces) {
        expect(cls, `${rel}: a floating surface is rendered INSIDE the frame. Under glass `
          + 'the frame is a backdrop root, so this cannot occlude — it will show the page '
          + 'through itself. Portal it (see `Dropdown` in components/ui/context-menu.tsx).')
          .not.toMatch(/\b(absolute|fixed|sticky)\b/);
      }
    }
  });

  it('and the tag scanner finds a Card that never says surface', () => {
    // The control on the scanner itself, with the exact shape that
    // defeated the first version: the `>` inside `render={<ul />}`.
    const fixture = '<Card padding="none" className="absolute left-0" render={<ul />} role="listbox">';
    expect(cardClassNames(fixture)).toEqual(['className="absolute left-0"']);
    expect(cardClassNames('<div className="absolute" />'), 'it matched something that is not a Card')
      .toEqual([]);
  });
});
