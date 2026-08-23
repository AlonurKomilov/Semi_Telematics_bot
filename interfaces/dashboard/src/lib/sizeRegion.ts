import type { CSSProperties } from 'react';
import type { SizeRegion } from '../preferences/registry';

/**
 * Scope the Size multipliers to one REGION of the app.
 *
 * Spread the result onto the element that already owns that region —
 * DataGrid's card, the sidebar's <aside>, a dialog's popup — and every
 * length inside it picks up that region's multiplier on top of the
 * global one. `tailwind.config.js` emits each length as
 * `calc(step × var(--size-<axis>, 1) × var(--size-region, 1))`, so the
 * multiplication happens at the point of use and nothing here has to
 * know which axis anything rides.
 *
 * A STYLE, not a wrapper component, on purpose: the surfaces that own a
 * region are already flex parents, grid items and scroll containers, and
 * slipping a <div> between them and their children is how a layout
 * silently loses `flex-1`, `min-h-0` or a sticky context. This adds no
 * DOM at all.
 *
 * The value is a `var()` reference rather than a number so the cascade
 * keeps doing the work: ThemeContext publishes `--size-region-<name>` on
 * <html> when the user moves that slider, and nothing re-renders.
 * Unset, `var(…, 1)` is the identity — a region with no preference costs
 * exactly nothing.
 *
 * Nesting REPLACES rather than compounds: an overlay opened from inside
 * the assistant renders at the overlay region's scale, not at both. That
 * is the intended reading — a region names where you are, and you are in
 * one place at a time.
 */
export function sizeRegion(name: SizeRegion): CSSProperties {
  return { '--size-region': `var(--size-region-${name}, 1)` } as CSSProperties;
}
