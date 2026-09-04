/**
 * The injector — the one thing that turns a theme SELECTOR into a mods
 * ENGINE.
 *
 * Until this existed, the runtime could write exactly three data
 * attributes and four size multipliers. Every colour in the product was a
 * literal in `index.css`, and a mod could only pick among blocks that
 * were already there. That is a picker. An engine has to be able to
 * install a value nobody wrote at build time.
 *
 * Three decisions are load-bearing.
 *
 * **A stylesheet, not inline properties.** Inline custom properties on
 * `<html>` beat every selector, which sounds convenient until you meet
 * the `@media print` reset: 44 literals that put the light palette back
 * so a dark-mode user does not print white-on-nothing. It carries no
 * `!important` — no COLOUR in this codebase does; the one exception is
 * the reduced-motion floor, which sets durations — so an inline token
 * silently defeats it and a dark mod prints dark. Wrapping the injected
 * rule in `@media screen` sidesteps that entirely: the rule does not
 * exist during print, and the reset keeps winning by being the only
 * thing there.
 *
 * **Appended last, at `:root`.** `:root` and `.dark` are both (0,1,0), so
 * source order decides and a sheet appended to `<head>` wins.
 *
 * That is enough for every token except the accent, and the accent is
 * the one a person actually asks for by name. `--primary` is also
 * declared in the `[data-accent]` blocks, which outrank `:root` — so an
 * injected accent lost, and lost SILENTLY under purple, green and azure
 * while appearing to work under blue, the one accent with no block.
 *
 * The fix is not more weight here. The two halves of that axis are not
 * even the same weight as each other (light (0,3,0), dark (0,2,0)), so
 * there is no single rank the injector could take that sits above both
 * and still below nothing else. Instead the preset stands DOWN: writing
 * `--primary` stamps `data-mod-accent`, and every accent block carries
 * `:not([data-mod-accent])`. A preset and an authored colour are two
 * answers to one question; ranking them was the wrong model.
 *
 * **Validated, because a mod is DATA.** Today the packs are ours. The
 * entire point of the arc is that they will not always be: the owner's
 * requirement is per-user authoring, stored in the browser. A value that
 * reaches a stylesheet is code the moment it can contain a closing brace,
 * so the boundary is here, from the first commit, rather than added later
 * when there is user input to be nervous about.
 */

/** The element id. One sheet, replaced in place — never appended twice. */
const STYLE_ID = 'mod-tokens';

/**
 * The attribute that tells the app something changed.
 *
 * Three MutationObservers watch the theme and read token values into
 * JavaScript — the WebGL scene in truck-anatomy, the DataGrid's canvas
 * paths, and the radius reader. Every one of them observes ATTRIBUTES on
 * `<html>`:
 *
 *   truck-anatomy/colors.ts  ['class','data-theme','data-accent']
 *   DataGrid.tsx             [… ,'data-radius','style']
 *   lib/radius.ts            [… ,'data-radius','style']
 *
 * Adding a `<style>` element to `<head>` fires NONE of them, not even
 * the two that already list `style` — that filter is about the style
 * ATTRIBUTE on the observed element, not about stylesheets. So an
 * injected palette would repaint the CSS and leave every JS-side reader
 * holding the previous theme's colours: a 3D truck in last week's blue.
 *
 * Stamping a hash of the sheet is the cheap fix. It rides machinery that
 * already exists in all three places rather than inventing a
 * subscription, and hashing rather than counting means re-applying an
 * identical palette is a no-op instead of a spurious repaint.
 */
const MOD_ATTR = 'data-mod';

/**
 * The attribute that stands the accent preset down. See the header, and
 * the `:not([data-mod-accent])` on every accent block in `index.css` —
 * this name is half of a contract whose other half is a stylesheet.
 *
 * Presence-only. There is nothing to say beyond "somebody else owns the
 * accent now", and a value would invite a second selector keyed on it.
 *
 * Stamped for `--primary` alone, not for the pack's other two tokens.
 * The block is all-or-nothing — standing it down for a mod that supplied
 * only `--primary-hover` would drop `--primary` back to base blue, which
 * is a colour nobody chose. The cost is that hover and text alone still
 * lose under a preset; `derivePalette` emits all three together, so the
 * only way to reach that corner is to hand-author a set that says half a
 * sentence. `accentCascade.test.ts` pins the corner so it stays known.
 */
const ACCENT_ATTR = 'data-mod-accent';

/** The token whose presence means the mod owns the accent. */
const ACCENT_TOKEN = '--primary';

/** djb2, enough to notice a changed declaration. Not a checksum. */
const digest = (s: string): string => {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
};

/**
 * What a mod may install.
 *
 * The same blast radius `palette.ts` already enforces, for the same
 * reason: a red that means "danger" cannot also be somebody's brand red,
 * and chart separation is a property of the whole ramp rather than of any
 * one slot. Surfaces, text, boundaries, the accent family and the new
 * material axis — nothing else.
 */
export const MOD_TOKENS: readonly string[] = [
  // surfaces and their inks
  '--background', '--foreground',
  '--card', '--card-foreground', '--popover', '--popover-foreground',
  '--secondary', '--secondary-foreground', '--muted', '--muted-foreground',
  '--accent', '--accent-foreground',
  '--border', '--input',
  '--sidebar', '--sidebar-foreground', '--sidebar-accent',
  '--sidebar-accent-foreground', '--sidebar-border',
  // the accent family
  '--primary', '--primary-foreground', '--primary-hover', '--primary-text',
  // the material axis
  '--surface-alpha', '--surface-blur', '--surface-saturate', '--surface-shadow',
];

const ALLOWED = new Set(MOD_TOKENS);

/**
 * Whether a value is safe to put in a stylesheet.
 *
 * A character allowlist rather than a blocklist, because a blocklist is a
 * list of the attacks somebody thought of. Excluding `;` and `}` makes
 * breaking out of the declaration impossible; excluding `:` blocks every
 * URL scheme, which is why `url()` cannot appear even by accident. That
 * also means this function deliberately CANNOT express a wallpaper — an
 * image is bytes with a fetch behind it, and it needs its own reviewed
 * path rather than a hole in this one.
 *
 * The length cap is not about safety, it is about a mod that pastes a
 * novel into a token and makes the sheet unreadable in devtools.
 */
const VALUE = /^[a-zA-Z0-9\s.,%#()/_+-]{1,200}$/;

/**
 * The functions a theme is made of. An ALLOWLIST, because the character
 * grammar alone is not enough — `attr(onload)` passes it, and the guard
 * caught that on the first run. The right model is not "the attacks I
 * thought of" but "the vocabulary a colour actually needs", and that
 * vocabulary is short.
 */
const FUNCTIONS = new Set([
  'oklch', 'oklab', 'lab', 'lch', 'rgb', 'rgba', 'hsl', 'hsla', 'hwb',
  'color', 'color-mix', 'var', 'calc', 'min', 'max', 'clamp',
]);

export function isSafeValue(v: unknown): v is string {
  if (typeof v !== 'string') return false;
  const s = v.trim();
  if (!VALUE.test(s)) return false;
  // Belt and braces. `url` cannot form a fetch without a colon, but a
  // reader should not have to reason that far to believe the line.
  if (/url|expression|import/i.test(s)) return false;
  // Every `name(` in the value must be one we recognise.
  for (const m of s.matchAll(/([a-zA-Z][a-zA-Z0-9-]*)\s*\(/g))
    if (!FUNCTIONS.has(m[1].toLowerCase())) return false;
  return true;
}

/** A token name a mod may set — on the list, and syntactically a custom
 *  property, so a widened list can never smuggle in a real property. */
export function isModToken(name: unknown): name is string {
  return typeof name === 'string' && /^--[a-z][a-z0-9-]*$/.test(name) && ALLOWED.has(name);
}

export interface ApplyResult {
  /** Declarations actually written. */
  applied: number;
  /** Names refused because they are not on the list. */
  rejectedNames: string[];
  /** Names whose VALUE failed the grammar. */
  rejectedValues: string[];
}

/**
 * Install a mod's tokens, replacing whatever the last call installed.
 *
 * Passing `null` removes the sheet entirely, which is what "no mod" means
 * — not an empty rule, which would still be a (0,1,0) declaration sitting
 * ahead of nothing.
 *
 * Rejections are RETURNED rather than thrown. A mod with one bad value
 * should install its other nineteen; a caller that wants to be strict can
 * read the result and say so. Throwing here would mean one typo in a
 * user-authored mod blanks their whole theme.
 */
export function applyModTokens(
  tokens: Record<string, string> | null,
  doc: Document = document,
  /**
   * Per-place overrides — `{ loads: { '--background': '…' } }`.
   *
   * Emitted as `:root[data-surface="loads"]`, which is (0,2,0) and so
   * beats the global `:root` block outright rather than by source
   * order. That matters because the two blocks live in one sheet and a
   * future reorder must not silently change which wins.
   *
   * These carry surface colours only — never `--primary`. A per-place
   * accent would have to out-rank the `[data-accent]` blocks the way
   * the global one does, and the `:not([data-mod-accent])` stand-down
   * that makes that work is written per BLOCK, not per surface: a
   * scoped accent would lose under purple, green and azure while
   * appearing to work under blue, which is the exact failure this
   * file's header records. The accent stays global on purpose.
   */
  surfaces?: Record<string, Record<string, string>> | null,
): ApplyResult {
  const result: ApplyResult = { applied: 0, rejectedNames: [], rejectedValues: [] };
  const existing = doc.getElementById(STYLE_ID);

  // "Nothing to install" has to count BOTH halves. Checking only the
  // global set meant a person who themed one place and left the app's
  // own colours alone got their sheet removed on every render — the
  // scoped blocks were built and then never reached.
  const hasScoped = Object.keys(surfaces ?? {}).length > 0;
  if ((!tokens || Object.keys(tokens).length === 0) && !hasScoped) {
    existing?.remove();
    delete doc.documentElement.dataset.mod;
    doc.documentElement.removeAttribute(ACCENT_ATTR);
    return result;
  }

  const decls: string[] = [];
  let ownsAccent = false;
  for (const [name, value] of Object.entries(tokens ?? {})) {
    if (!isModToken(name)) { result.rejectedNames.push(String(name)); continue; }
    if (!isSafeValue(value)) { result.rejectedValues.push(name); continue; }
    // After both gates, never before: a rejected `--primary` has not been
    // installed, and standing the preset down for it would leave the app
    // with no accent at all.
    if (name === ACCENT_TOKEN) ownsAccent = true;
    decls.push(`${name}: ${value.trim()};`);
  }
  result.applied = decls.length;

  if (decls.length === 0 && !hasScoped) {
    existing?.remove();
    delete doc.documentElement.dataset.mod;
    doc.documentElement.removeAttribute(ACCENT_ATTR);
    return result;
  }

  // `@media screen` is the whole print story — see the header.
  // A `:root` block is emitted only when there is something to put in
  // it: an empty rule is still a rule, and one sitting ahead of the
  // scoped blocks reads as though the global look were being set.
  const blocks = decls.length
    ? [`  :root {\n    ${decls.join('\n    ')}\n  }`]
    : [];
  for (const [id, own] of Object.entries(surfaces ?? {})) {
    // Same two gates as the global set. A surface is stored data like
    // any other, and "it came from our own picker" is not a property
    // this function can check.
    if (!/^[a-z][a-z0-9-]*$/.test(id)) { result.rejectedNames.push(String(id)); continue; }
    const scoped: string[] = [];
    for (const [name, value] of Object.entries(own)) {
      if (!isModToken(name)) { result.rejectedNames.push(String(name)); continue; }
      if (!isSafeValue(value)) { result.rejectedValues.push(name); continue; }
      scoped.push(`${name}: ${value.trim()};`);
    }
    if (!scoped.length) continue;
    result.applied += scoped.length;
    blocks.push(`  :root[data-surface="${id}"] {\n    ${scoped.join('\n    ')}\n  }`);
  }
  const css = `@media screen {\n${blocks.join('\n')}\n}`;

  const el = existing ?? doc.createElement('style');
  if (!existing) {
    el.id = STYLE_ID;
    // Last in <head>, so it wins the (0,1,0) tie against `.dark` on
    // source order. Appending to <body> would work too and would look
    // like a bug to the next reader.
    doc.head.appendChild(el);
  }
  el.textContent = css;
  // Before the digest: the digest is what wakes the three observers that
  // read tokens back into JavaScript, so the accent must already be
  // resolved by the time they look.
  if (ownsAccent) doc.documentElement.setAttribute(ACCENT_ATTR, '');
  else doc.documentElement.removeAttribute(ACCENT_ATTR);
  // Last, so an observer that reads tokens back sees the new sheet
  // already in the document.
  doc.documentElement.setAttribute(MOD_ATTR, digest(css));
  return result;
}

/** The sheet's current text, or null. Exported for the guards — nothing
 *  in the app should be reading its own stylesheet back. */
export function modStyleText(doc: Document = document): string | null {
  return doc.getElementById(STYLE_ID)?.textContent ?? null;
}

/**
 * A seed, turned into exactly the tokens a mod may install.
 *
 * The bridge between the two halves that already existed and had no
 * caller: `derivePalette` knows how to turn a canvas and an accent into
 * 24 legible tokens, and `applyModTokens` knows how to install a value
 * safely. This is the two-line join, and it is here rather than in a
 * component so the whole path can be tested without rendering one.
 *
 * The filter is not defensive padding. `derivePalette` is OUR code and
 * its output is trustworthy, but the list it may write to is the
 * contract — and a token added to the palette without being added here
 * should fail loudly at the boundary rather than half-install.
 */
export function seedTokens(
  seed: { mode: 'dark' | 'light'; canvas: string; brand: string },
  derive: (s: typeof seed) => Record<string, string> | null,
): Record<string, string> | null {
  const palette = derive(seed);
  if (!palette) return null;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(palette)) if (isModToken(k)) out[k] = v;
  return Object.keys(out).length ? out : null;
}
