/**
 * What each mods axis still does NOT reach — measured, and ratcheted.
 *
 * A coverage audit is a photograph. This is the thing that keeps the
 * photograph true: every rule below counts the sites an axis cannot
 * reach, and pins that count. The pins are today's measured numbers, not
 * targets.
 *
 * IT ASSERTS A COUNT AND PRINTS A PERCENTAGE, and the split is
 * deliberate. A percentage needs a denominator, and the denominator
 * moves every time anybody adds a file — so a pinned percentage goes red
 * on innocent work, and a guard that cries wolf gets deleted. The COUNT
 * of things an axis cannot reach is stable: it only changes when
 * somebody adds one or fixes one.
 *
 * TWO-WAY, like the raw-input ratchet in `components/ui/chrome.test.ts`:
 * going UP fails (a new hardcoded thing), and going DOWN fails too, with
 * the number to write. The second half is the point — an improvement
 * nobody pins is an improvement that comes back.
 *
 * Every rule carries a positive control. A scan that quietly stops
 * matching reports zero debt and reads as a clean bill of health, which
 * is the worst failure mode a file like this has.
 *
 * NOT DUPLICATED HERE: colour literals (chrome.test.ts guard 35), the
 * icon door (test/iconLane.test.ts), the corner ramp
 * (mods/theme/corners.test.ts), raw lengths (chrome.test.ts). Those axes
 * are already held; a second copy would drift from the first.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');

/** Every source file a person's setting is supposed to reach. */
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === 'node_modules' || name === 'dist') continue;
      sources(full, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

const FILES = sources(SRC).map((f) => ({
  rel: relative(SRC, f),
  text: readFileSync(f, 'utf8'),
}));

/** Line number of an offset, so a finding can be opened. */
const lineAt = (text: string, index: number) => text.slice(0, index).split('\n').length;

interface Rule {
  readonly axis: string;
  /** What ONE violation is, in a sentence a stranger can act on. */
  readonly what: string;
  /** Today's measured count. Not a target. */
  readonly pin: number;
  readonly find: RegExp;
  /** Everything the axis DOES reach, for the printed percentage. Left
   *  out where an honest denominator needs hand-classification. */
  readonly reaches?: RegExp;
  /** A string that must match, and one that must not. */
  readonly control: readonly [string, string];
}

const RULES: readonly Rule[] = [
  {
    axis: 'cursor',
    what: 'a cursor utility behind a variant prefix — the pack ships `.cursor-x`, '
      + 'which cannot match the class token `disabled:cursor-x`',
    pin: 34,
    find: /\b[a-z-]+:cursor-[a-z-]+/g,
    reaches: /(?<![a-z:-])cursor-[a-z-]+/g,
    control: ['disabled:cursor-not-allowed', 'cursor-pointer'],
  },
  {
    axis: 'cursor',
    what: 'a cursor written imperatively — an inline style beats every stylesheet '
      + 'rule, and the pack is forbidden `!important`',
    pin: 8,
    find: /style\.cursor\s*=/g,
    control: ['document.body.style.cursor = "auto"', 'className="cursor-grab"'],
  },
  {
    axis: 'material',
    what: 'a popover-coloured surface painted by hand — `.surface` is what the '
      + 'material axis reaches, and these files re-type the colour instead',
    // 22 when the audit measured it, 2 now: twenty surfaces across
    // eleven files moved onto `surface surface-popover`. The two left
    // are the scroll arrows inside the Select popup, which paint the
    // colour to OCCLUDE the list sliding under them — a translucent
    // occluder occludes nothing. `mods/theme/material.test.ts` holds
    // them as a named exemption with that reason.
    //
    // OCCURRENCES, not files. The first pin here was 12 — a `grep -l`
    // count — against an assertion that counts every match. A pin read
    // from a different counter than the one that checks it is not a pin.
    pin: 2,
    find: /bg-popover/g,
    control: ['className="bg-popover border"', 'className="surface"'],
  },
  {
    axis: 'motion',
    what: 'a chart series that never names a duration, so it animates at the '
      + "library's hardcoded 1500ms while every control around it scales",
    pin: 22,
    find: /<(?:Bar|Line|Area|Pie|Radar)\b/g,
    control: ['<Bar dataKey="x" />', '<BarChart>'],
  },
  {
    axis: 'size',
    what: 'a frozen box on a chart — the text inside scales with the Size axis '
      + 'and the box does not, so raising Size shows LESS',
    pin: 15,
    find: /(?:height|width)=\{\d+\}/g,
    control: ['height={220}', 'height={size}'],
  },
];

/** Counted once, so the report and the assertions agree. */
const MEASURED = RULES.map((rule) => {
  const hits: string[] = [];
  let reached = 0;
  for (const { rel, text } of FILES) {
    for (const m of text.matchAll(rule.find)) {
      hits.push(`${rel}:${lineAt(text, m.index ?? 0)} → ${m[0]}`);
    }
    if (rule.reaches) reached += [...text.matchAll(rule.reaches)].length;
  }
  return { rule, hits, reached };
});

describe('what the mods axes still cannot reach', () => {
  for (const { rule, hits } of MEASURED) {
    it(`${rule.axis}: ${rule.what}`, () => {
      const n = hits.length;
      expect(n, `${rule.axis}: ${n - rule.pin} NEW site(s) the axis cannot reach.\n`
        + `Newest first:\n${hits.slice(-5).join('\n')}`)
        .toBeLessThanOrEqual(rule.pin);
      // The other direction. An improvement nobody writes down is an
      // improvement that comes back — and a pin left above the truth
      // hides the next regression inside its own slack.
      expect(n, `${rule.axis}: this got BETTER — ${rule.pin} → ${n}. `
        + `Lower the pin to ${n} in the same commit.`)
        .toBeGreaterThanOrEqual(rule.pin);
    });

    it(`${rule.axis}: and that scan can fail`, () => {
      const [hit, miss] = rule.control;
      expect([...hit.matchAll(rule.find)].length, `the scan stopped matching "${hit}"`)
        .toBeGreaterThan(0);
      expect([...miss.matchAll(rule.find)].length, `the scan now matches "${miss}"`)
        .toBe(0);
    });
  }

  it('finds a codebase to measure', () => {
    // A walk that returns nothing reports zero debt everywhere, which
    // reads as a clean bill of health.
    expect(FILES.length, 'the source walk found nothing').toBeGreaterThan(400);
  });

  it('reports where every axis stands', () => {
    const rows = MEASURED.map(({ rule, hits, reached }) => {
      const total = rule.reaches ? reached + hits.length : undefined;
      const pct = total ? ((reached / total) * 100).toFixed(1) + '%' : '—';
      return `  ${rule.axis.padEnd(9)} ${String(hits.length).padStart(4)} unreached`
        + `  ${pct.padStart(7)} covered  ${rule.what.slice(0, 52)}…`;
    });
    // Printed, never asserted: the percentage moves with the size of the
    // codebase, and pinning it would go red on innocent work.
    // eslint-disable-next-line no-console
    console.log(`\nmods coverage — ${FILES.length} source files\n${rows.join('\n')}\n`);
    expect(rows.length).toBe(RULES.length);
  });
});

describe('an axis that is deliberately inert says so', () => {
  it('no typeface pack moves the mono family', () => {
    // 103 `font-mono` sites are inert to every pack, and that is
    // probably right — a monospace column is a column BECAUSE it is
    // monospace. But it was never written down anywhere, so adding one
    // line to a pack file would flip every DataGrid numeric cell, every
    // VIN and every id with nothing going red.
    const packs = readdirSync(join(SRC, 'mods/store/items/font'))
      .filter((f) => f.endsWith('.css'));
    expect(packs.length, 'no font packs found — this checks nothing').toBeGreaterThan(2);
    for (const f of packs) {
      const css = readFileSync(join(SRC, 'mods/store/items/font', f), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '');
      expect(css, `font/${f} moves --font-mono. That is a product decision, not a `
        + 'tidy-up: it flips every numeric column in the product. Make it deliberately '
        + 'or not at all.').not.toMatch(/--font-mono\s*:/);
    }
  });
});
