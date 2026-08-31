/**
 * The colour values themselves — is what we ship legible, and is it even
 * paintable?
 *
 * Everything else that guards colour in this repo checks PLUMBING: that a
 * class compiles, that a token is re-pointed by every preset, that print
 * puts the light palette back. None of it looks at a ratio. A token can
 * be wired perfectly and still be a white label on a pale amber fill.
 *
 * Two guards, and the second is a precondition for the first:
 *
 *   sRGB gamut — an oklch value outside sRGB is not painted as written.
 *     The browser gamut-maps it, so the colour on screen has a different
 *     lightness and chroma than the file says, and any contrast sum done
 *     on the authored value is arithmetic about a colour nobody sees.
 *     That is why the ratios below are computed AFTER mapping, with the
 *     CSS Color 4 algorithm (chroma reduction with a local clip inside a
 *     0.02 OKLab JND) rather than a naive per-channel clamp.
 *
 *   contrast — the token pairs the app renders, in every one of the six
 *     theme cells the mode/accent split produces. "Token pairs", not
 *     every pair: PAIRS is a hand-written list, so a combination nobody
 *     added is a combination nobody checks. It covers the surfaces and
 *     their foregrounds, the tone system on all three grounds, the two
 *     primitives' soft variants, and the call-site alphas that carry a
 *     label (`bg-destructive/10`, `bg-primary/15`). It does NOT walk the
 *     class names, so a feature file inventing its own fill is invisible
 *     — 496 inline `bg-<token>/N` fills exist and only the sharpest are
 *     listed. Widening it is cheap; add a row and give whatever fails a
 *     reason.
 *
 * Three things this gets right that a first attempt does not:
 *
 *   1. An alpha fill is composited over its GROUND, as the browser does.
 *      `--ok-bg` is `--ok` at 15%, so a tone pill on a card is a
 *      different colour from the same pill on the canvas — and measuring
 *      only against the canvas certifies real failures as clean.
 *   2. Thresholds differ by ROLE. 4.5 for text, 3.0 for large text and
 *      non-text UI (WCAG 1.4.11). One number for both either drowns in
 *      1.2:1 hairlines or misses the label on a button.
 *   3. It cannot land green, and pretending otherwise would be the
 *      failure mode. Every known failure is listed below WITH the reason
 *      it is still there. That list is the work queue: delete an entry
 *      when you fix the pair, never add one to quiet a new regression.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

// ── colour maths, self-contained ────────────────────────────────────
// No dependency: a package added for a guard is a package in the test
// loop, and this is ~60 lines.
type RGB = [number, number, number];

const oklabToLinear = (L: number, a: number, b: number): RGB => {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ];
};

const linearToOklab = ([r, g, b]: RGB): RGB => {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ];
};

const encode = (c: number) =>
  c <= 0.0031308 ? 12.92 * c : 1.055 * Math.max(c, 0) ** (1 / 2.4) - 0.055;
const inGamut = ([r, g, b]: RGB, eps = 1e-6) =>
  [r, g, b].every((c) => c >= -eps && c <= 1 + eps);
const clamp = ([r, g, b]: RGB): RGB =>
  [r, g, b].map((c) => Math.min(1, Math.max(0, c))) as RGB;

/** CSS Color 4 gamut mapping. Returns the painted colour, and whether
 *  the authored one needed mapping at all. */
function oklchToSrgb(L: number, C: number, H: number): { rgb: RGB; mapped: boolean } {
  const h = (H * Math.PI) / 180;
  const lin = oklabToLinear(L, C * Math.cos(h), C * Math.sin(h));
  if (inGamut(lin)) return { rgb: clamp(lin).map(encode) as RGB, mapped: false };
  if (L <= 0) return { rgb: [0, 0, 0], mapped: true };
  if (L >= 1) return { rgb: [1, 1, 1], mapped: true };
  let lo = 0, hi = C, best = clamp(lin);
  const JND = 0.02;
  while (hi - lo > 1e-5) {
    const mid = (lo + hi) / 2;
    const cand = oklabToLinear(L, mid * Math.cos(h), mid * Math.sin(h));
    if (inGamut(cand)) { lo = mid; best = clamp(cand); continue; }
    const clipped = clamp(cand);
    const [dl, da, db] = linearToOklab(clipped);
    const d = Math.hypot(dl - L, da - mid * Math.cos(h), db - mid * Math.sin(h));
    if (d < JND) { best = clipped; lo = mid; } else { hi = mid; }
  }
  return { rgb: best.map(encode) as RGB, mapped: true };
}
const relLum = ([r, g, b]: RGB) => {
  const f = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const ratio = (a: RGB, b: RGB) => {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/** What the browser paints when `fg` at `alpha` sits on `bg`. */
const over = (fg: RGB, alpha: number, bg: RGB): RGB =>
  fg.map((c, i) => c * alpha + bg[i] * (1 - alpha)) as RGB;

// ── token resolution ────────────────────────────────────────────────
type Val =
  | { kind: 'colour'; L: number; C: number; H: number; alpha: number }
  | { kind: 'ref'; name: string; alpha: number };

function parseValue(raw: string): Val | null {
  const v = raw.trim();
  let m = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)%?\s*)?\)$/.exec(v);
  if (m) {
    const a = m[4] === undefined ? 1 : (v.includes('%') ? Number(m[4]) / 100 : Number(m[4]));
    return { kind: 'colour', L: +m[1], C: +m[2], H: +m[3], alpha: a };
  }
  m = /^color-mix\(\s*in oklab,\s*var\((--[a-z0-9-]+)\)\s*([\d.]+)%\s*,\s*transparent\s*\)$/.exec(v);
  if (m) return { kind: 'ref', name: m[1], alpha: Number(m[2]) / 100 };
  m = /^var\((--[a-z0-9-]+)\)$/.exec(v);
  if (m) return { kind: 'ref', name: m[1], alpha: 1 };
  return null;                       // shadows, fonts, numbers — not colours
}

/** Values that LOOK like a colour but the parser could not read. These
 *  are the dangerous ones: an unreadable token is silently dropped, and
 *  every pair that used it disappears with it — a green run that proves
 *  nothing. `oklch(95% …)`, legal CSS, does exactly that. */
const UNREADABLE: string[] = [];
const LOOKS_LIKE_COLOUR = /oklch|color-mix|\brgba?\(|\bhsla?\(|#[0-9a-fA-F]{3,8}\b|\bvar\(/;

const declsOf = (body: string): Record<string, Val> => {
  const out: Record<string, Val> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) {
    const v = parseValue(m[2]);
    if (v) out[m[1]] = v;
    // A shadow carries a colour but is not one — and `\bpx\b` does not
    // match `4px`, there being no word boundary between a digit and a
    // letter, which is why the first version reported all four.
    else if (LOOKS_LIKE_COLOUR.test(m[2]) && !/px|shadow/.test(m[2])) {
      const line = `${m[1]}: ${m[2].trim()}`;
      if (!UNREADABLE.includes(line)) UNREADABLE.push(line);
    }
  }
  return out;
};
const blocks = (re: RegExp) =>
  [...CSS.matchAll(re)].map((m) => m[m.length - 1]).join('\n');

const ROOT = declsOf(blocks(/^ {2}:root \{$([\s\S]*?)^ {2}\}$/gm));
const DARK = declsOf(blocks(/^ {2}\.dark \{$([\s\S]*?)^ {2}\}$/gm));
const accentBlock = (mode: 'light' | 'dark', accent: string) =>
  declsOf(blocks(new RegExp(
    `^ {2}${mode === 'light' ? ':root:not\\(\\.dark\\)' : '\\.dark'}` +
    `\\[data-accent="${accent}"\\] \\{$([\\s\\S]*?)^ {2}\\}$`, 'gm')));

const ACCENTS = ['blue', 'purple', 'green'] as const;
const CELLS = (['light', 'dark'] as const).flatMap((mode) =>
  ACCENTS.map((accent) => ({
    name: `${mode} ${accent}`,
    tokens: {
      ...ROOT,
      ...(mode === 'dark' ? DARK : {}),
      ...accentBlock(mode, accent),
    } as Record<string, Val>,
  })));

/** Flatten a token to a painted colour plus its own alpha. */
function resolve(tokens: Record<string, Val>, name: string, depth = 0):
  { rgb: RGB; alpha: number; mapped: boolean } | null {
  if (depth > 4) return null;
  const v = tokens[name];
  if (!v) return null;
  if (v.kind === 'ref') {
    const inner = resolve(tokens, v.name, depth + 1);
    return inner && { ...inner, alpha: inner.alpha * v.alpha };
  }
  const { rgb, mapped } = oklchToSrgb(v.L, v.C, v.H);
  return { rgb, alpha: v.alpha, mapped };
}

// ── the pairs the app actually renders ──────────────────────────────
// `ground` is what an alpha fill composites over — a tone pill on a card
// is a different colour from the same pill on the canvas.
type Role = 'text' | 'ui';
const FLOOR: Record<Role, number> = { text: 4.5, ui: 3.0 };
const TONES = ['ok', 'warn', 'danger', 'info'] as const;

/**
 * `bgAlpha` is a CALL-SITE alpha — `bg-destructive/10`, `bg-primary/15`.
 * The token itself is opaque; the class fades it. Without this the guard
 * can only see fills whose alpha is baked into the token, and the
 * primitives' own soft variants would be invisible to it.
 */
type Pair = { fg: string; bg: string; ground?: string; role: Role; bgAlpha?: number };
const PAIRS: Pair[] = [
  { fg: '--foreground', bg: '--background', role: 'text' },
  { fg: '--card-foreground', bg: '--card', role: 'text' },
  { fg: '--popover-foreground', bg: '--popover', role: 'text' },
  { fg: '--primary-foreground', bg: '--primary', role: 'text' },
  // The hover state of the same button — a control must not become
  // harder to read because the pointer is over it.
  { fg: '--primary-foreground', bg: '--primary-hover', role: 'text' },
  { fg: '--secondary-foreground', bg: '--secondary', role: 'text' },
  { fg: '--accent-foreground', bg: '--accent', role: 'text' },
  { fg: '--destructive-foreground', bg: '--destructive', role: 'text' },
  { fg: '--sidebar-foreground', bg: '--sidebar', role: 'text' },
  { fg: '--muted-foreground', bg: '--muted', role: 'text' },
  { fg: '--muted-foreground', bg: '--background', role: 'text' },
  { fg: '--muted-foreground', bg: '--card', role: 'text' },
  { fg: '--muted-foreground', bg: '--popover', role: 'text' },
  // Solid tone fills carry the tone foreground…
  ...TONES.map((t) => ({ fg: `--${t}-foreground`, bg: `--${t}`, role: 'text' as Role })),
  // …and the soft pill is the tone AS TEXT on its own 15% wash, which
  // has to be composited over both surfaces it appears on.
  ...TONES.flatMap((t) => (['--background', '--card', '--popover'] as const).map((g) => ({
    fg: `--${t}`, bg: `--${t}-bg`, ground: g, role: 'text' as Role,
  }))),
  // …and bare, as an icon or a number.
  ...TONES.flatMap((t) => (['--background', '--card', '--popover'] as const).map((g) => ({
    fg: `--${t}`, bg: g, role: 'text' as Role,
  }))),
  // Non-text: WCAG 1.4.11 wants 3.0 for a control boundary or a
  // graphical object carrying meaning.
  { fg: '--ring', bg: '--background', role: 'ui' },
  { fg: '--ring', bg: '--card', role: 'ui' },
  { fg: '--input', bg: '--background', role: 'ui' },
  { fg: '--input', bg: '--card', role: 'ui' },
  // `text-primary` is body text — ~270 sites, including the link variant
  // of both primitives — so it is judged at 4.5, not the 3.0 an accent
  // shape would get. It resolves to `--primary-text`, NOT `--primary`:
  // tailwind.config overrides textColor.primary for exactly this reason.
  ...(['--background', '--card', '--popover', '--sidebar'] as const).map((g) => ({
    fg: '--primary-text', bg: g, role: 'text' as Role,
  })),
  // The primitives' soft destructive variant: `bg-destructive/10
  // text-destructive`, and /20 in dark. This is what button.tsx and
  // badge.tsx actually render; the solid pair above has 7 call sites.
  ...(['--background', '--card', '--popover'] as const).flatMap((g) => [
    { fg: '--destructive-text', bg: '--destructive', ground: g, bgAlpha: 0.1, role: 'text' as Role },
    { fg: '--destructive-text', bg: '--destructive', ground: g, bgAlpha: 0.2, role: 'text' as Role },
  ]),
  // …and bare, which is what `text-destructive` alone renders.
  ...(['--background', '--card', '--popover'] as const).map((g) => ({
    fg: '--destructive-text', bg: g, role: 'text' as Role,
  })),
  // The selected chip — sidebar nav, the theme picker's own chip, a
  // DataGrid column pill. It used to put the accent in the LABEL on a
  // wash of itself, which measured 3.96 at worst and, more quietly, left
  // "selected" resting on a fill 1.13:1 from its ground — a cue almost
  // nobody could see. The label is plain now and the accent moved to a
  // ring, so both halves are measured: the text on the wash…
  ...(['--background', '--card', '--popover', '--sidebar'] as const).flatMap((g) =>
    [0.1, 0.15, 0.2].map((a) => ({
      fg: '--foreground', bg: '--primary', ground: g, bgAlpha: a, role: 'text' as Role,
    }))),
  // …and the ring that says WHICH one is selected. Non-text, so 3.0 —
  // and only the FULL-strength accent reaches it: /20, /30, /40 and even
  // /60 sit between 1.21 and 2.61, which is why every existing chip
  // border was raised rather than kept.
  ...(['--background', '--card', '--popover', '--sidebar'] as const).map((g) => ({
    fg: '--primary', bg: g, role: 'ui' as Role,
  })),
  ...[1, 2, 3, 4, 5].flatMap((n) => (['--background', '--card'] as const).map((g) => ({
    fg: `--chart-${n}`, bg: g, role: 'ui' as Role,
  }))),
];

/**
 * Pairs that fail today, each with the reason it is still failing.
 *
 * This is a WORK QUEUE, not an allowlist. Delete an entry when you fix
 * the pair — the guard fails if a listed pair starts passing, so the
 * list cannot rot. Adding one to quiet a NEW regression is the single
 * thing that would make this guard worthless, so an entry needs a reason
 * a reviewer can argue with. "known" is not a reason.
 */
  'probably right, and that is a design decision, not a nudge.';
const BOUNDARY_REDESIGN =
  'deferred 6.2 — `--input` is a hairline, and the whole surface ladder ' +
  '(canvas 0.10 -> chrome 0.215 -> card 0.275 -> popover 0.32) is built on ' +
  'hairlines that read as separations rather than component edges. Raising ' +
  'it is a visible redesign across 784 `border-border` sites, and nobody ' +
  'has yet classified which of them are decorative dividers (WCAG 1.4.11 ' +
  'exempts those) versus real control boundaries. `--input` is the subset ' +
  'that is genuinely in scope — a form field edge — and it is 6 uses.';

  'is a new token, not a nudge.';
  'every accent cell and both primitives.';

  '`text-foreground`.';

const KNOWN: Record<string, string> = Object.fromEntries([
  // Hairlines. A visible redesign, not a nudge — see 6.2.
  ...([
    'dark blue | --input on --background',
    'dark blue | --input on --card',
    'dark green | --input on --background',
    'dark green | --input on --card',
    'dark purple | --input on --background',
    'dark purple | --input on --card',
    'light blue | --input on --background',
    'light blue | --input on --card',
    'light green | --input on --background',
    'light green | --input on --card',
    'light purple | --input on --background',
    'light purple | --input on --card',
  ] as const).map((k) => [k, BOUNDARY_REDESIGN] as const),
]);

/**
 * Tokens authored outside sRGB, with the overflow each one currently
 * carries. A RATCHET: a new clipping token fails, and an existing one
 * that clips harder fails. Fixing one means writing the chroma that
 * survives — which changes nothing on an sRGB display, because the
 * browser is already painting that value.
 */
const KNOWN_GAMUT: Record<string, number> = {
  // dark blue
  'dark blue | --chart-1': 0.0057,
  'dark blue | --chart-3': 0.0055,
  'dark blue | --chart-5': 0.0176,
  'dark blue | --primary': 0.0057,
  'dark blue | --swatch-accent-blue': 0.0057,
  // dark green
  'dark green | --chart-3': 0.0055,
  'dark green | --chart-5': 0.0176,
  'dark green | --swatch-accent-blue': 0.0057,
  // dark purple
  'dark purple | --chart-3': 0.0055,
  'dark purple | --chart-5': 0.0176,
  'dark purple | --swatch-accent-blue': 0.0057,
  // light blue
  'light blue | --chart-3': 0.0065,
  'light blue | --chart-5': 0.0265,
  'light blue | --destructive': 0.0096,
  'light blue | --warn': 0.0017,
  // light green
  'light green | --chart-3': 0.0065,
  'light green | --chart-5': 0.0265,
  'light green | --destructive': 0.0096,
  'light green | --warn': 0.0017,
  // light purple
  'light purple | --chart-3': 0.0065,
  'light purple | --chart-5': 0.0265,
  'light purple | --destructive': 0.0096,
  'light purple | --warn': 0.0017,
};

const key = (cell: string, p: Pair) =>
  `${cell} | ${p.fg} on ${p.bg}${p.bgAlpha ? `/${p.bgAlpha * 100}` : ''}`
  + `${p.ground ? ` over ${p.ground}` : ''}`;

describe('colour values', () => {
  // A token this file cannot read is dropped, and every pair that used
  // it disappears with it — the suite stays green and proves nothing.
  // `oklch(95% 0.05 100)`, which is legal CSS Color 4, does exactly that,
  // and so does a hex. This is the guard on the guard.
  it('reads every colour value in index.css', () => {
    expect(
      UNREADABLE,
      'these look like colours and the parser could not read them, so every ' +
        'pair using them is silently skipped. Teach parseValue the shape',
    ).toEqual([]);
  });

  it('every rendered pair clears its WCAG floor', () => {
    const failures: string[] = [];
    const fixed: string[] = [];
    const generated = new Set<string>();
    for (const cell of CELLS) {
      for (const p of PAIRS) {
        const fg = resolve(cell.tokens, p.fg);
        const bgRaw = resolve(cell.tokens, p.bg);
        if (!fg || !bgRaw) continue;          // token absent in this cell
        const ground = p.ground ? resolve(cell.tokens, p.ground) : null;
        // An alpha fill is what the browser composites, not what the file
        // says — a tone pill on a card is a different colour from the
        // same pill on the canvas. Without a ground we cannot judge it.
        // A token's own alpha and the call site's multiply, exactly as
        // tokenColor()'s nested color-mix does.
        const alpha = bgRaw.alpha * (p.bgAlpha ?? 1);
        if (alpha < 1 && !ground) continue;
        const bg = alpha < 1 && ground
          ? over(bgRaw.rgb, alpha, ground.rgb)
          : bgRaw.rgb;
        const fgC = fg.alpha < 1 ? over(fg.rgb, fg.alpha, bg) : fg.rgb;
        const r = ratio(fgC, bg);
        const k = key(cell.name, p);
        generated.add(k);
        if (r < FLOOR[p.role]) {
          if (!(k in KNOWN)) failures.push(`${k} — ${r.toFixed(2)}:1, needs ${FLOOR[p.role]}`);
        } else if (k in KNOWN) {
          fixed.push(`${k} — now ${r.toFixed(2)}:1; delete its KNOWN entry`);
        }
      }
    }
    // A KNOWN entry naming no generated pair is an excuse for a check
    // that no longer happens — rename a token and three real checks and
    // their three excuses vanish together, silently.
    const inert = Object.keys(KNOWN).filter((k) => !generated.has(k));
    expect(
      inert,
      'these KNOWN entries match no pair this guard generates — the check ' +
        'they excuse is gone, so the excuse is too',
    ).toEqual([]);
    expect(fixed, 'a listed failure now passes — delete it from KNOWN').toEqual([]);
    expect(
      failures,
      'these pairs are rendered and do not meet their floor. Fix the value, ' +
        'or add a KNOWN entry saying WHY it stays',
    ).toEqual([]);
  });

  it('no colour token drifts further outside sRGB', () => {
    // An out-of-gamut value is not the colour on screen: the browser maps
    // it, so the file documents a colour nobody has seen and every ratio
    // above would be arithmetic about the wrong one. It also makes a
    // retune silently inert — nudge the chroma of a clipped token and
    // nothing moves except on a P3 display.
    const maxChroma = (L: number, H: number) => {
      let lo = 0, hi = 0.5;
      for (let i = 0; i < 50; i++) {
        const mid = (lo + hi) / 2;
        const h = (H * Math.PI) / 180;
        if (inGamut(oklabToLinear(L, mid * Math.cos(h), mid * Math.sin(h)))) lo = mid;
        else hi = mid;
      }
      return lo;
    };
    const worse: string[] = [];
    const better: string[] = [];
    // Keyed per CELL, not per token. Taking the max across cells lets a
    // regression in one cell hide behind a worse value in another —
    // pushing dark --chart-5 from 0.0176 to 0.0256 stayed green because
    // the light cell's 0.0265 held the maximum.
    const seen = new Map<string, number>();
    for (const cell of CELLS) {
      for (const [name, v] of Object.entries(cell.tokens)) {
        if (v.kind !== 'colour') continue;
        const over_ = v.C - maxChroma(v.L, v.H);
        if (over_ <= 0) continue;
        const k = `${cell.name} | ${name}`;
        seen.set(k, Math.max(seen.get(k) ?? 0, over_));
      }
    }
    for (const [name, over_] of seen) {
      const allowed = KNOWN_GAMUT[name];
      if (allowed === undefined) {
        worse.push(`${name} is outside sRGB by ${over_.toFixed(4)} and is not listed`);
      } else if (over_ > allowed + 1e-4) {
        worse.push(`${name} now overflows ${over_.toFixed(4)}, ceiling ${allowed}`);
      }
    }
    for (const [name, allowed] of Object.entries(KNOWN_GAMUT)) {
      const now = seen.get(name);
      if (now === undefined) better.push(`${name} is in gamut now — delete its entry`);
      else if (now < allowed - 5e-4) {
        better.push(`${name} overflows only ${now.toFixed(4)}, ceiling says ${allowed} — lower it`);
      }
    }
    expect(better, 'the gamut ratchet has gone stale').toEqual([]);
    expect(
      worse,
      'lower the chroma to the value that survives mapping — on an sRGB ' +
        'display that changes nothing, because it is already what paints',
    ).toEqual([]);
  });

  // The chart ramp is five CATEGORIES, so what matters is not each
  // colour's contrast but whether any two can be told apart. Slot 1
  // follows the accent, which means a preset can walk one series onto
  // another: with a green accent, slot 1 landed on the same hue 142 as
  // slot 2 and the two adjacent series became one colour — dE2000 6.70
  // on dark, 7.79 on light.
  //
  // It is not only charts. 41 `*-chart-N` classes across 7 feature files
  // use the ramp as a categorical BADGE palette, and two of them put the
  // colliding slots side by side — settings/WorkHours' owner and admin
  // rows, scorecards/DriverInsights' compliance and efficiency.
  //
  // Measured post-gamut-mapping: five of these tokens are authored
  // outside sRGB, so a distance computed in oklch would claim separation
  // the screen does not have.
  it('no two chart series collide, in any theme cell', () => {
    const toLab = (rgb: RGB): [number, number, number] => {
      const inv = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
      const [r, g, b] = rgb.map(inv);
      const X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b;
      const Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
      const Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b;
      const f = (t: number) => (t > (6 / 29) ** 3 ? Math.cbrt(t) : t / (3 * (6 / 29) ** 2) + 4 / 29);
      const [fx, fy, fz] = [f(X / 0.95047), f(Y), f(Z / 1.08883)];
      return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
    };
    const de2000 = (c1: [number, number, number], c2: [number, number, number]) => {
      const [L1, a1, b1] = c1, [L2, a2, b2] = c2;
      const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2), Cb = (C1 + C2) / 2;
      const G = Cb > 0 ? 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) : 0;
      const a1p = (1 + G) * a1, a2p = (1 + G) * a2;
      const C1p = Math.hypot(a1p, b1), C2p = Math.hypot(a2p, b2);
      const h1 = ((Math.atan2(b1, a1p) * 180) / Math.PI + 360) % 360;
      const h2 = ((Math.atan2(b2, a2p) * 180) / Math.PI + 360) % 360;
      const dLp = L2 - L1, dCp = C2p - C1p;
      let dh = 0;
      if (C1p * C2p !== 0) {
        dh = h2 - h1;
        if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
      }
      const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin((dh * Math.PI) / 360);
      const Lb = (L1 + L2) / 2, Cbp = (C1p + C2p) / 2;
      let hbp: number;
      if (C1p * C2p === 0) hbp = h1 + h2;
      else if (Math.abs(h1 - h2) <= 180) hbp = (h1 + h2) / 2;
      else hbp = h1 + h2 < 360 ? (h1 + h2 + 360) / 2 : (h1 + h2 - 360) / 2;
      const rad = (d: number) => (d * Math.PI) / 180;
      const T = 1 - 0.17 * Math.cos(rad(hbp - 30)) + 0.24 * Math.cos(rad(2 * hbp))
        + 0.32 * Math.cos(rad(3 * hbp + 6)) - 0.20 * Math.cos(rad(4 * hbp - 63));
      const SL = 1 + (0.015 * (Lb - 50) ** 2) / Math.sqrt(20 + (Lb - 50) ** 2);
      const SC = 1 + 0.045 * Cbp, SH = 1 + 0.015 * Cbp * T;
      const RT = -2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
        * Math.sin(rad(60 * Math.exp(-(((hbp - 275) / 25) ** 2))));
      return Math.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2
        + RT * (dCp / SC) * (dHp / SH));
    };

    // Today's minimum is 26.89. 20 leaves room to retune without
    // pretending a pair at 21 is as distinct as one at 40.
    const FLOOR_DE = 20;
    const tight: string[] = [];
    for (const cell of CELLS) {
      const ramp = [1, 2, 3, 4, 5].map((n) => resolve(cell.tokens, `--chart-${n}`));
      for (let i = 0; i < 5; i++) {
        for (let j = i + 1; j < 5; j++) {
          const a = ramp[i], b = ramp[j];
          if (!a || !b) continue;
          const d = de2000(toLab(a.rgb), toLab(b.rgb));
          if (d < FLOOR_DE) {
            tight.push(`${cell.name} | chart-${i + 1} vs chart-${j + 1} — dE2000 ${d.toFixed(2)}`);
          }
        }
      }
    }
    expect(
      tight,
      'two series this close read as one colour. If an accent has walked ' +
        'slot 1 onto another slot, that slot takes the hue the accent ' +
        'displaced — see the accent blocks in index.css',
    ).toEqual([]);
  });
});
