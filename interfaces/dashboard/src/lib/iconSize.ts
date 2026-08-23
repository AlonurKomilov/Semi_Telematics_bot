/**
 * Pixel icon size -> the Tailwind class that renders it.
 *
 * lucide's `size` prop writes width/height ATTRIBUTES on the <svg>. No
 * multiplier can reach an attribute, so an icon written that way is the
 * one thing on screen that stays put while everything around it scales —
 * measured: 16px at 1x, still 16px at 1.5x. The equivalent CLASS rides
 * `--size-control` and comes out at 24px.
 *
 * Call sites migrated to the class directly. This map exists for the
 * WRAPPERS that take a numeric `size` prop from their own callers
 * (InfoTip, PoiIcon, EventIcon…) and cannot know the number until
 * runtime — it is the single place that translation happens.
 *
 * The ladder is design.md §7 / CLAUDE.md: 12 · 14 · 16 · 18 · 20 · 24.
 * `size-4.5` (18px) is not on Tailwind's spacing scale and is declared
 * in tailwind.config.js for exactly this reason.
 */
const LADDER: ReadonlyArray<readonly [number, string]> = [
  [10, 'size-2.5'], [12, 'size-3'], [14, 'size-3.5'], [16, 'size-4'],
  [18, 'size-4.5'], [20, 'size-5'], [24, 'size-6'], [28, 'size-7'],
  [32, 'size-8'], [40, 'size-10'], [48, 'size-12'],
];

/**
 * Nearest step, not an exact lookup: a wrapper's default may be
 * off-ladder (KnowledgeBase's category icon defaults to 13) and a caller
 * may pass anything. Returning the closest rung keeps the icon within a
 * pixel of what it was AND makes it scale, where a strict lookup would
 * have to fall back to one fixed size and silently resize it.
 */
export function iconSizeClass(px: number): string {
  let best = LADDER[0];
  for (const step of LADDER) {
    if (Math.abs(step[0] - px) < Math.abs(best[0] - px)) best = step;
  }
  return best[1];
}
