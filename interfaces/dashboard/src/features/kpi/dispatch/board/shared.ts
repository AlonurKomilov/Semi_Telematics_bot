/**
 * What the board's two panes must AGREE on.
 *
 * The unit pane and the days pane render the same rows side by side
 * without sharing a DOM ancestor, so their row height is a contract,
 * not a coincidence — and a number or a date spelled differently on
 * one side than the other is the same defect as a misaligned row.
 */


export function usd(v: number): string {
  return `$${v.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

/** Rounded money — for tooltips and gaps, where cents are noise. */
export const money0 = (v: number) => `$${Math.round(v).toLocaleString()}`;

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const dayLabel = (iso: string) => {
  const d = new Date(`${iso}T00:00:00Z`);
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
};


/** "Woodland, CA 1425734" → "Woodland, CA" (best-effort tidy). */
export const place = (s: string) => s.replace(/\s+\d+$/, '').trim();

/** The row-height CONTRACT: both panes render every row at this
 *  height, so the two columns stay in step down a 69-row board. */
export const ROW_H = 'h-36';

// Isolation walls, not lazy rendering: layout+paint containment tells
// the browser nothing inside a row affects anything outside it, so a
// hover flip or repaint while scrolling costs one row, never the
// whole section (a 10-truck section is thousands of nodes — without
// walls it was one giant invalidation zone, and big sections
// stuttered where small ones felt fine).  Unlike the removed
// content-visibility, rows still render up front — page scrolling
// stays plain compositing.  Safe because rows are fixed-height and
// nothing visual escapes them (tooltips and menus portal to <body>).
export const ROW_CONTAIN = { contain: 'layout paint' } as React.CSSProperties;
