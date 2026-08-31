/**
 * The injector is the security boundary of the whole mods arc, so these
 * guards are written against the day the packs stop being ours.
 *
 * Today a mod is a row in a TypeScript array that we wrote. The stated
 * goal is per-user authoring — someone types a value, it is stored in
 * their browser, and it ends up in a stylesheet. A value that reaches a
 * stylesheet is code the moment it can carry a closing brace, so every
 * escape below is tested now rather than when there is user input to be
 * nervous about.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  applyModTokens, modStyleText, isSafeValue, isModToken, MOD_TOKENS,
} from './modStyle';
import { DERIVED_TOKENS } from './palette';

const sheet = () => document.getElementById('mod-tokens');

beforeEach(() => { document.getElementById('mod-tokens')?.remove(); });

describe('installing tokens', () => {
  it('writes a rule and puts it last in head', () => {
    const r = applyModTokens({ '--card': '#101418' });
    expect(r.applied).toBe(1);
    expect(sheet()).not.toBeNull();
    // Last child, because `:root` and `.dark` are both (0,1,0) and source
    // order is the only thing that decides between them.
    expect(document.head.lastElementChild).toBe(sheet());
    expect(modStyleText()).toContain('--card: #101418;');
  });

  it('wraps everything in @media screen', () => {
    // The entire print story. The `@media print` reset in index.css puts
    // the light palette back through 44 literals and carries no
    // `!important` — nothing in this codebase does. A rule that applies
    // during print would beat it and a dark mod would print dark.
    applyModTokens({ '--background': '#000000' });
    const css = modStyleText()!;
    expect(css.startsWith('@media screen')).toBe(true);
    expect(css).toContain(':root {');
  });

  it('replaces rather than accumulating', () => {
    applyModTokens({ '--card': '#111111' });
    applyModTokens({ '--card': '#222222' });
    applyModTokens({ '--card': '#333333' });
    expect(document.querySelectorAll('#mod-tokens').length).toBe(1);
    expect(modStyleText()).toContain('#333333');
    expect(modStyleText()).not.toContain('#111111');
  });

  it('removes the sheet for "no mod", rather than leaving an empty rule', () => {
    applyModTokens({ '--card': '#111111' });
    applyModTokens(null);
    expect(sheet()).toBeNull();
    applyModTokens({ '--card': '#111111' });
    applyModTokens({});
    expect(sheet()).toBeNull();
  });
});

describe('what a value may contain', () => {
  const BREAKOUTS = [
    '#fff } body { display: none',        // close the rule and write another
    '#fff; --foreground: #fff',           // a second declaration
    'url(https://x/y.png)',               // a fetch
    'url(data:image/svg+xml;base64,AAA)', // a fetch with a payload
    'red; background: url(//x)',
    'var(--x) }',
    '#fff/**/;color:red',                 // comment-hidden second declaration
    'expression(alert(1))',
    '@import "x"',
    'attr(onload)',                       // any function we did not name
    'counter(x)',
    'element(#y)',
    'image-set(a)',
    'rgb(0 0 0) <script>',
  ];

  it('refuses every way out of a declaration', () => {
    for (const bad of BREAKOUTS)
      expect(isSafeValue(bad), JSON.stringify(bad)).toBe(false);
  });

  it('refuses the declaration separators on their own', () => {
    // Tested independently, and that distinction cost a mutation round.
    // The BREAKOUTS above all carry two illegal characters at once, so
    // widening the grammar to admit `;` left every one of them still
    // failing — on the colon. A guard that only ever sees compound
    // examples cannot tell you which exclusion is load-bearing.
    //
    // On its own a semicolon is not exploitable: it ends the
    // declaration, and starting another needs a colon. It is refused
    // anyway, because a grammar whose safety depends on three
    // exclusions interacting is one nobody can check by reading it.
    for (const bad of ['#fff;', '#fff}', '#fff{', '#fff;;'])
      expect(isSafeValue(bad), JSON.stringify(bad)).toBe(false);
  });

  it('refuses any function outside the colour vocabulary', () => {
    // A character grammar alone is not enough: `attr(onload)` passes it,
    // and this suite caught that on its first run. The allowlist is the
    // vocabulary a colour needs, which is short — not the attacks
    // somebody thought of, which is never finished.
    expect(isSafeValue('oklch(0.5 0.1 200)')).toBe(true);
    expect(isSafeValue('attr(x)')).toBe(false);
    expect(isSafeValue('paint(worklet)')).toBe(false);
    expect(isSafeValue('env(safe-area-inset-top)')).toBe(false);
  });

  it('refuses a URL scheme by refusing the colon', () => {
    // Not a `url()` blocklist — a colon is what every scheme needs, and
    // no colour or length value has one. This is also why the injector
    // cannot express a wallpaper: an image is bytes with a fetch behind
    // it and needs its own reviewed path, not a hole in this one.
    expect(isSafeValue('http://x')).toBe(false);
    expect(isSafeValue('data:text/css,x')).toBe(false);
    expect(isSafeValue('javascript:1')).toBe(false);
  });

  it('refuses what is not a string, and what is absurdly long', () => {
    for (const bad of [null, undefined, 42, {}, [], true])
      expect(isSafeValue(bad)).toBe(false);
    expect(isSafeValue('#fff'.repeat(80))).toBe(false);
    expect(isSafeValue('')).toBe(false);
    expect(isSafeValue('   ')).toBe(false);
  });

  it('accepts the values a theme is actually made of', () => {
    for (const good of [
      '#0a0a0a', '#fff', 'oklch(0.62 0.108 214)', 'oklch(1 0 0 / 12%)',
      'rgb(255 255 255 / 40%)', 'color-mix(in oklab, var(--ok) 12%, transparent)',
      'var(--primary)', 'transparent', 'currentColor', 'none',
      '12px', '1.5rem', '0.72', '70%',
      '0 1px 2px rgb(0 0 0 / 10%), 0 8px 24px -12px rgb(0 0 0 / 20%)',
    ]) expect(isSafeValue(good), good).toBe(true);
  });
});

describe('what a mod may name', () => {
  it('refuses a token that is not on the list', () => {
    const r = applyModTokens({ '--danger': '#00ff00', '--card': '#111111' });
    expect(r.rejectedNames).toEqual(['--danger']);
    expect(r.applied).toBe(1);
    expect(modStyleText()).not.toContain('--danger');
  });

  it('refuses anything that is not a custom property at all', () => {
    for (const bad of ['color', 'background', '-moz-binding', '--', '--A', ''])
      expect(isModToken(bad), JSON.stringify(bad)).toBe(false);
  });

  it('keeps the meaning layer and the chart ramp out of reach', () => {
    // The same blast radius palette.ts enforces. A red that means
    // "danger" cannot also be somebody's brand red, and chart separation
    // is a property of the whole ramp rather than of any one slot.
    const forbidden = [
      '--ok', '--warn', '--danger', '--info',
      '--ok-bg', '--warn-bd', '--danger-foreground', '--info-bg',
      '--destructive', '--destructive-text', '--destructive-foreground',
      '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
      '--radius', '--size-text', '--size-control', '--font-sans',
      '--swatch-accent-blue', '--sidebar-w',
    ];
    for (const t of forbidden)
      expect(MOD_TOKENS, `${t} is reachable by a mod`).not.toContain(t);
  });

  it('covers everything the palette derives, and the material axis', () => {
    // If derivePalette gains a token and this list does not, an account
    // palette silently ships with that one token missing — the hardest
    // kind of theme bug to see, because 23 of 24 look right.
    for (const t of DERIVED_TOKENS)
      expect(MOD_TOKENS, `derivePalette emits ${t} but no mod can install it`).toContain(t);
    for (const t of ['--surface-alpha', '--surface-blur', '--surface-saturate', '--surface-shadow'])
      expect(MOD_TOKENS).toContain(t);
  });
});

describe('one bad value does not blank a theme', () => {
  it('installs the good declarations and reports the rest', () => {
    const r = applyModTokens({
      '--card': '#101418',
      '--popover': 'red } body { display:none',
      '--muted': '#1a1f24',
      '--nope': '#fff',
    });
    expect(r.applied).toBe(2);
    expect(r.rejectedValues).toEqual(['--popover']);
    expect(r.rejectedNames).toEqual(['--nope']);
    const css = modStyleText()!;
    expect(css).toContain('--card');
    expect(css).toContain('--muted');
    expect(css).not.toContain('display');
    // And the sheet is still one well-formed rule, not a torn one.
    expect((css.match(/\{/g) || []).length).toBe((css.match(/\}/g) || []).length);
  });
});
