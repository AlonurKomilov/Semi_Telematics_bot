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
import { readFileSync } from 'node:fs';
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

describe('the four sides of the frame are one object', () => {
  it('there are four of them', () => {
    // Left, top, right, bottom. Not a taste: fewer means a side has
    // gone back to being padding, more means something else started
    // painting the chrome and nobody decided whether it is frame.
    expect(sides().map((s) => s.where)).toHaveLength(4);
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
