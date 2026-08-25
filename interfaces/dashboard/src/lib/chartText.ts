/**
 * Chart text does not reach the Size engine the way the rest of the page
 * does. Recharts takes `fontSize` as a number and writes it onto an SVG
 * <text> as an attribute, so no Tailwind class ever touches it — every
 * axis, tick and legend on the dashboard sat frozen at 10–12px while the
 * headings beside them grew. At 130% a chart's own labels were the
 * smallest text on the screen.
 *
 * These are the same calc() the Tailwind config emits for a text-axis
 * length, written out by hand because the value has to arrive as a style
 * or an attribute rather than a class. Measured in Chrome: an SVG
 * `font-size` attribute does resolve calc() and var() (0.6875rem under
 * --size-text:2 computes to 22px), and the custom properties cascade into
 * SVG — so a chart inside a region picks that region up for free.
 */
const chartFont = (rem: number) =>
  `calc(${rem}rem * var(--size-text, 1) * var(--size-region, 1))`;

/** 9px at 100% — a marginal note on a chart, not a label. */
export const CHART_FONT_2XS = chartFont(0.5625);
/** 10px at 100% — dense axes with many ticks. */
export const CHART_FONT_XS = chartFont(0.625);
/** 11px at 100% — the default for ticks and legends. */
export const CHART_FONT_SM = chartFont(0.6875);
/** 12px at 100% — labels that carry a value rather than a scale. */
export const CHART_FONT_MD = chartFont(0.75);
