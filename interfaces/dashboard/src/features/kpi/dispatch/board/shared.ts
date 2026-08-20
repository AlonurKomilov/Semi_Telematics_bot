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

// Rows outside the viewport skip layout and paint; the intrinsic size
// is the row's exact rendered height, so scrolling never shifts.
export const CV_ROW = {
  contentVisibility: 'auto', containIntrinsicSize: 'auto 9rem',
} as React.CSSProperties;

