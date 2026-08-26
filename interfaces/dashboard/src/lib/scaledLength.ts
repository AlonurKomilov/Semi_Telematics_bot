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
