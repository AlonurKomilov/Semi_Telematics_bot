/**
 * Motion is two claims, and the second one is the interesting half.
 *
 * The first: every duration rides a multiplier, so one token reaches all
 * 189 `transition-*` utilities with no call site changing.
 *
 * The second: the INFINITE loops do not. `animate-spin` compiles to
 * `animation: spin 1s linear infinite` — the duration lives in the
 * shorthand, which `animationDuration` never touches. If they were on
 * the axis, a "snappy" setting would turn 89 spinners and 18 pulses into
 * a strobe, and the reduced-motion floor would make it a 10ms one. That
 * is the failure this file exists to prevent, and it is invisible in
 * every other test because nothing renders a spinner and measures it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { MOTION_SCALE, motionPercent } from '../catalogue';
import { MOTION_PACKS } from '../store/items/motion';

const ROOT = join(__dirname, '..', '..', '..');
const CONFIG = readFileSync(join(ROOT, 'tailwind.config.js'), 'utf8');
const CSS = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8');
/** Comments blanked in place so offsets and line numbers survive. */
/**
 * The engine sheet PLUS the speed items — the browser assembles both
 * through `@import`, and since the speeds moved out of `index.css` a
 * guard reading only the engine would find no `[data-motion]` block at
 * all and say so about nothing.
 */
const MOTION_DIR = join(__dirname, '..', 'store', 'items', 'motion');
const CSS_ALL = CSS + readdirSync(MOTION_DIR)
  .filter((f) => f.endsWith('.css'))
  .map((f) => readFileSync(join(MOTION_DIR, f), 'utf8')).join('\n');
const CODE = CSS_ALL.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));

describe('every duration rides the multiplier', () => {
  it('scales transitions and the animate plugin, from one helper', () => {
    for (const key of ['transitionDuration', 'animationDuration'])
      expect(CONFIG, `${key} is no longer scaled`)
        .toMatch(new RegExp(`${key}:\\s*scaleMotion\\(`));
    const helper = /const scaleMotion = [\s\S]*?\n\n/.exec(CONFIG)?.[0] ?? '';
    expect(helper, 'scaleMotion is gone').not.toBe('');
    expect(helper).toContain('var(--motion-scale, 1)');
    // calc(), not a table of literals per setting — so a mod can dial
    // any value the injector accepts rather than picking from three.
    expect(helper).toContain('calc(');
  });

  it('leaves the easing reachable too', () => {
    expect(CONFIG).toMatch(/transitionTimingFunction:\s*\{[^}]*var\(--motion-ease/);
  });
});

describe('what must stay OFF the axis', () => {
  it('never redefines the keyframe animations', () => {
    // `animation` and `keyframes` in theme.extend would put `spin`,
    // `pulse`, `ping` and `bounce` under our control — and under the
    // multiplier. They must keep Tailwind's own shorthand, where the
    // duration is baked and unreachable.
    const extend = CONFIG.slice(CONFIG.indexOf('extend:'));
    for (const key of ['animation:', 'keyframes:'])
      expect(extend, `theme.extend.${key} puts the infinite loops on the motion axis`)
        .not.toMatch(new RegExp(`\\n\\s{4,8}${key}`));
  });

  it('ends the loop rather than speeding it up, under reduced motion', () => {
    const block = /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/.exec(CODE)?.[1];
    expect(block, 'there is no reduced-motion floor').toBeTruthy();
    // The load-bearing line. Scaling an infinite animation toward zero
    // is not "no motion" — it is the same motion, faster. Only ending
    // the loop stops it.
    expect(block!, 'the iteration count is not capped — this is a strobe, not a stop')
      .toMatch(/animation-iteration-count:\s*1\s*!important/);
    expect(block!).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(block!).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it('keeps that floor above anything a mod can write', () => {
    // A guarantee to someone whose vestibular system is at stake is not
    // a preference to be outranked. It is the only `!important` in the
    // stylesheet, and it must stay that way in both directions: still
    // there, and still the only one.
    const bangs = [...CODE.matchAll(/!important/g)].map((m) => m.index!);
    const start = CODE.indexOf('@media (prefers-reduced-motion: reduce)');
    const end = CODE.indexOf('}', CODE.indexOf('}', start) + 1) + 1;
    for (const at of bangs)
      expect(at >= start && at <= end + 200,
        `an !important at offset ${at} is outside the reduced-motion floor`).toBe(true);
    expect(bangs.length, 'the floor lost its !important').toBeGreaterThan(2);
  });
});

describe('the axis itself', () => {
  it('ships a neutral default and two named settings', () => {
    expect(CODE).toMatch(/--motion-scale:\s*1;/);
    expect(CODE).toMatch(/--motion-ease:\s*cubic-bezier/);
    for (const [name, dir] of [['calm', 1], ['snappy', -1]] as const) {
      const m = new RegExp(`\\[data-motion="${name}"\\]\\s*\\{[^}]*--motion-scale:\\s*([\\d.]+)`).exec(CODE);
      expect(m, `no [data-motion="${name}"] block`).not.toBeNull();
      const v = Number(m![1]);
      // Calm is slower than default, snappy is faster. A sign flip here
      // is the kind of thing that reads fine and feels wrong.
      expect(dir > 0 ? v > 1 : v < 1, `${name} scales the wrong way (${v})`).toBe(true);
      expect(v, `${name} is not a sane multiplier`).toBeGreaterThan(0);
    }
  });
});

/**
 * The panel says how much motion there is. It can only say it from a
 * copy of numbers that live in a stylesheet, so the copy has to be
 * watched — the same bargain the pre-paint script makes with applyTheme.
 */
describe('the percentage the panel shows', () => {
  it('matches the multipliers index.css actually ships', () => {
    for (const [name, scale] of Object.entries(MOTION_SCALE)) {
      if (name === 'default') {
        // No attribute block — the neutral value is the bare :root one.
        const m = /:root\s*\{[^}]*--motion-scale:\s*([\d.]+)/.exec(CODE);
        expect(m, ':root declares no --motion-scale').not.toBeNull();
        expect(Number(m![1]), 'the neutral scale drifted').toBe(scale);
        continue;
      }
      const m = new RegExp(`\\[data-motion="${name}"\\]\\s*\\{[^}]*--motion-scale:\\s*([\\d.]+)`).exec(CODE);
      expect(m, `no [data-motion="${name}"] block — MOTION_SCALE names a setting the CSS does not`).not.toBeNull();
      expect(Number(m![1]), `${name}: the panel would show a number the app does not use`).toBe(scale);
    }
  });

  it('reads as MORE motion, not as a longer duration', () => {
    // The trap this exists for: --motion-scale multiplies DURATION, so
    // calm is the BIGGER number while being the setting that moves
    // least. Printed raw it would be the only percentage on the card
    // where a higher number means less of the thing named.
    expect(motionPercent('calm'), 'calm reads as more motion than default').toBeLessThan(100);
    expect(motionPercent('default')).toBe(100);
    expect(motionPercent('snappy'), 'snappy reads as less motion than default').toBeGreaterThan(100);
    // The exact figures, so a change to either half is deliberate.
    expect(motionPercent('calm')).toBe(63);
    expect(motionPercent('snappy')).toBe(167);
  });

  it('prints a number rather than NaN when handed a value the axis lost', () => {
    expect(motionPercent('normal' as never)).toBe(100);
  });
});

describe('typography joins the token layer', () => {
  it('puts the mono family on a token, reaching every font-mono site', () => {
    expect(CODE, '--font-mono is not declared').toMatch(/--font-mono:/);
    expect(CONFIG, 'tailwind mono does not read the token')
      .toMatch(/mono:\s*\['var\(--font-mono\)'/);
  });

  it('closes the font-sans escape hatch', () => {
    // No site writes `font-sans` today, so this changes nothing now — it
    // stops the first one that does from silently bypassing the token
    // the whole app inherits from `html`.
    expect(CONFIG).toMatch(/sans:\s*\['var\(--font-sans\)'/);
  });
});

describe('a speed may ship its own curve', () => {
  /** Each item's file, by id, with comments stripped — a file that
   *  EXPLAINS it may not reach for `!important` would otherwise read as
   *  one that does. */
  const FILES = Object.fromEntries(readdirSync(MOTION_DIR)
    .filter((f) => f.endsWith('.css'))
    .map((f) => [f.replace(/\.css$/, ''),
      readFileSync(join(MOTION_DIR, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')]));

  it('the base curve is still a token, or nothing could override it', () => {
    expect(/:root\s*\{[^}]*--motion-ease:\s*cubic-bezier/.test(CODE),
      ':root declares no --motion-ease').toBe(true);
  });

  it('an item that claims a curve sets one, and one that does not does not', () => {
    // The claim is what the store tile reads, so it has to be true. It
    // is also the whole reason a speed is an item and not a value: a
    // scale is three numbers, a curve is character.
    const claimed = MOTION_PACKS.filter((m) => m.eases).map((m) => m.id);
    expect(claimed.length, 'no item ships a curve — motion gained nothing by becoming one')
      .toBeGreaterThan(0);
    for (const m of MOTION_PACKS) {
      if (m.id === 'default') continue;
      const file = FILES[m.id];
      expect(file, `motion/${m.id}.css is missing`).toBeTruthy();
      expect(/--motion-ease:/.test(file), `${m.id}: eases=${!!m.eases} disagrees with its file`)
        .toBe(!!m.eases);
    }
  });

  it('and no item reaches for the floor', () => {
    // The reduced-motion floor is the only `!important` in the system.
    for (const [id, file] of Object.entries(FILES))
      expect(file, `motion/${id}.css uses !important`).not.toMatch(/!important/);
  });
});
