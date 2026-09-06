/**
 * The one door to the icon set.
 *
 * Every file that draws an icon imports it from here rather than from
 * `lucide-react`, and `src/test/iconLane.test.ts` holds that line.
 *
 * WHY, GIVEN IT CHANGES NOTHING TODAY. An icon pack is a Mods axis that
 * cannot exist while 236 files name their glyphs at the library
 * directly: swapping the set would mean editing all of them, and
 * swapping only some would put two vocabularies on one screen — which
 * is the thing design.md's "no second icon set" rule is actually about.
 * One door makes the swap a change in one file instead of 236, and it
 * makes the mixed-set failure impossible rather than merely discouraged.
 *
 * It earns its keep before any pack ships, too. The size rule — an icon
 * is sized by CLASS, never by the `size` prop, because the prop writes
 * an `<svg>` attribute no Size multiplier can reach — has been a
 * convention with a lint rule behind it. A single door is where that
 * becomes checkable at the boundary rather than at 574 call sites.
 *
 * `export *` rather than a hand-written list of the 158 names in use:
 * a list is a second inventory that drifts, and lucide ships
 * `sideEffects: false` with an ESM build, so Rollup drops every glyph
 * nobody imports exactly as it did when the imports were direct. The
 * bundle is measured in the commit that introduced this, not assumed.
 */
export * from 'lucide-react';
export type { LucideIcon, LucideProps } from 'lucide-react';
