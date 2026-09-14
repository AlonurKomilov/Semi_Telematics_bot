import { useLayoutEffect, useState } from 'react';
/**
 * A length the Size engine can reach, for the handful of places a class
 * cannot go — an inline `style` object, mostly because a third-party
 * component takes its box as a style rather than a className.
 *
 * `tailwind.config.js` turns every dimension step into
 * `calc(step * var(--size-<axis>) * var(--size-region))` and picks the
 * axis by MAGNITUDE: up to 3rem is a control, up to 6rem is layout,
 * beyond that a panel. This mirrors that rule exactly, so `220px`
 * written here behaves like `h-55` would. Two spellings of one value
 * that drift apart are worse than one spelling nobody likes.
 */
const REM = 16;

type Axis = 'text' | 'control' | 'layout' | 'panel';

const axisFor = (rem: number): Axis =>
  rem <= 3 ? 'control' : rem <= 6 ? 'layout' : 'panel';

/**
 * @param px    the length at 100%, in CSS pixels
 * @param axis  override the magnitude rule. Use it when the length is
 *              driven by something other than its own size — a
 *              textarea's max-height is about how many LINES fit, so it
 *              follows `text` however tall the box happens to be.
 */
export function scaledPx(px: number, axis?: Axis): string {
  const rem = px / REM;
  return `calc(${rem}rem * var(--size-${axis ?? axisFor(rem)}, 1) * var(--size-region, 1))`;
}

/**
 * The same length as a NUMBER, for the props that refuse a string.
 *
 * `scaledPx` returns a `calc()`, which is what a style wants and what an
 * SVG attribute resolves. Recharts' `<ResponsiveContainer height>` is
 * typed `number | \`${number}%\`` and writes it onto a wrapper div, so a
 * calc never reaches it — seven chart boxes sat frozen while the text
 * inside them scaled, which is worse than not scaling either: at 150%
 * the labels grew inside an unchanged box and the chart showed LESS.
 *
 * Read live and re-read when the picker stamps `<html>`, the same
 * bargain `lib/radius.ts` makes: a custom-property change alone does not
 * re-render React.
 */
export function useScaledPx(px: number, axis: Axis = 'layout'): number {
  const [out, setOut] = useState(px);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const read = () => {
      const cs = getComputedStyle(root);
      const n = (name: string) => {
        const v = parseFloat(cs.getPropertyValue(name));
        return Number.isFinite(v) && v > 0 ? v : 1;
      };
      setOut(Math.round(px * n(`--size-${axis}`) * n('--size-region')));
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ['style', 'class', 'data-mod'] });
    return () => mo.disconnect();
  }, [px, axis]);
  return out;
}
