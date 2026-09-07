import type { PackMeta } from './packs/meta';
/**
 * The pointer itself.
 *
 * THE ONE AXIS THAT TAKES SOMETHING AWAY. Every other mod adds: a
 * colour, a corner, a pattern, a set of glyphs. A CSS cursor REPLACES
 * the operating system's, and the operating system's is where people
 * with low vision set a larger or higher-contrast pointer. Nothing in
 * CSS can read that setting — there is no `prefers-cursor` — so a pack
 * cannot honour it, only override it.
 *
 * What makes that acceptable is the shape mods already have: this is
 * per-user, per-device, and off by default. Nobody can set it for
 * anybody else, and the person who turns it on is choosing for their
 * own screen. An account-wide cursor would not be defensible; this is.
 *
 * IMAGES, and here that is unavoidable — unlike the wallpaper, which
 * became a pattern of tokens precisely because it could. A pointer is a
 * shape; there is no procedural equivalent. So every cursor ships as a
 * `data:` URI inside `index.css`: no network, no storage, no CSP
 * question, and nothing for `inject.ts` to have to allow.
 *
 * NINE KINDS, because a pack that changed the arrow and left the rest
 * to the OS would be two vocabularies on one screen — the same thing
 * the icon rule forbids, and more obvious here, since the arrow and the
 * hand are seen within a second of each other.
 */
export interface CursorPack extends PackMeta {
}

// The packs — the list and the CSS — live in `mods/packs/cursor/`.
// This file is the contract a pack has to keep.

/**
 * Every cursor this app asks for, measured rather than guessed: these
 * are the `cursor-*` utilities the source actually uses, and a pack has
 * to answer for all of them.
 *
 * `default` is the arrow — it is set on `:root` rather than through a
 * utility, because nothing writes `cursor-default` for the ordinary
 * case; it is what the page has when no rule says otherwise.
 */
export const CURSOR_KINDS = [
  'default', 'pointer', 'not-allowed', 'help',
  'grab', 'grabbing', 'wait', 'col-resize', 'crosshair',
] as const;
export type CursorKind = (typeof CURSOR_KINDS)[number];

/**
 * The largest a cursor image may be.
 *
 * Not a style rule. Browsers silently ignore a cursor over 128px, and
 * Windows is unreliable above 32 — an oversized one is not a big
 * pointer, it is NO pointer, which reads as the app having broken.
 */
export const CURSOR_MAX_PX = 32;
