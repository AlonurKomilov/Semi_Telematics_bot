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
 * source order decides and a sheet appended to `<head>` wins. It
 * deliberately does NOT outrank the accent blocks at (0,3,0) — a mod that
 * wants the accent changes the accent, through the pack it names.
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
): ApplyResult {
  const result: ApplyResult = { applied: 0, rejectedNames: [], rejectedValues: [] };
  const existing = doc.getElementById(STYLE_ID);

  if (!tokens || Object.keys(tokens).length === 0) {
    existing?.remove();
    return result;
  }

  const decls: string[] = [];
  for (const [name, value] of Object.entries(tokens)) {
    if (!isModToken(name)) { result.rejectedNames.push(String(name)); continue; }
    if (!isSafeValue(value)) { result.rejectedValues.push(name); continue; }
    decls.push(`${name}: ${value.trim()};`);
  }
  result.applied = decls.length;

  if (decls.length === 0) { existing?.remove(); return result; }

  // `@media screen` is the whole print story — see the header.
  const css = `@media screen {\n  :root {\n    ${decls.join('\n    ')}\n  }\n}`;

  const el = existing ?? doc.createElement('style');
  if (!existing) {
    el.id = STYLE_ID;
    // Last in <head>, so it wins the (0,1,0) tie against `.dark` on
    // source order. Appending to <body> would work too and would look
    // like a bug to the next reader.
    doc.head.appendChild(el);
  }
  el.textContent = css;
  return result;
}

/** The sheet's current text, or null. Exported for the guards — nothing
 *  in the app should be reading its own stylesheet back. */
export function modStyleText(doc: Document = document): string | null {
  return doc.getElementById(STYLE_ID)?.textContent ?? null;
}
