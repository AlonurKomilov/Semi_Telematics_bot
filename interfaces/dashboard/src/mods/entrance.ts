import type { ItemMeta } from './store/items/meta';

/**
 * How a routed page arrives.
 *
 * ONE WRAPPER, EVERY ROUTE. There is exactly one `<Outlet/>` and all six
 * shells go through it, so an entrance is a single decision about every
 * navigation in the app — which is why the switch defaults to OFF and
 * stays that way: this app is navigated dozens of times an hour, and a
 * movement on every one of them is a tax rather than a delight.
 *
 * The item carries CLASS NAMES rather than CSS, unlike every other
 * visual axis here, and that is the honest shape for this one: the
 * entrance is React-level (the wrapper does not exist until React
 * mounts, which is also why `entrance` is not stamped pre-paint), and
 * the utilities it needs already exist. The strings are LITERAL in each
 * item's file because Tailwind reads source text — a class assembled at
 * runtime is invisible to the scanner and would simply never be built.
 *
 * The duration rides `--motion-scale` like everything else, and the
 * reduced-motion floor turns it off entirely.
 */
export interface EntrancePack extends ItemMeta {
  /** What the page enters with. Literal, for the scanner. */
  readonly classes: string;
}

// The items — the list and the classes — live in
// `mods/store/items/entrance/`. This file is the contract one keeps.
