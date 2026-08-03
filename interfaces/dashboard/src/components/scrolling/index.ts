// ── components/scrolling — the single source of truth for scrolling ──
//
// Import from HERE, never from the files inside.  Full rules, and the
// list of things that deliberately do NOT belong to this module, are in
// ./CLAUDE.md.

// The REGION contract — what makes a scrolling div a proper scroll
// region: focusable, named, overscroll-contained, and padded away from
// whatever sticky chrome sits over it.
export { ScrollRegion, useScrollRegion } from './region';
export type { ScrollRegionOptions } from './region';

// The custom PAINTED bars.  Narrow by design: they exist because pinned
// columns make a full-width native bar lie about what scrolls.  A
// surface with no pinned columns wants a plain scroll region and the
// browser's own bar, which index.css already themes.
export {
  ScrollbarH,
  ScrollbarV,
  V_BAR_GUTTER,
  useOverflow,
  useWheelToHorizontal,
  HIDE_NATIVE_SCROLLBAR,
} from './scrollbars';
export type { ScrollMetrics } from './scrollbars';
