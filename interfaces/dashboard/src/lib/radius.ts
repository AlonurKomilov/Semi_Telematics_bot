/**
 * The Corners axis, for the places CSS cannot reach.
 *
 * `--radius` is a CSS custom property, so a class or a stylesheet reads it
 * for free. Two kinds of consumer cannot: a canvas/SVG library that takes
 * a NUMBER (recharts branches on `radius === +radius` and silently draws
 * square corners for a string), and anything computing geometry in JS.
 *
 * This is the Corners twin of `lib/chartText.ts` and `lib/scaledLength.ts`
 * on the Size axis. The reader below started life inside DataGrid.tsx,
 * which was the only component in the app doing this correctly; it lives
 * here now so there is one copy rather than one per author who needs it.
 */
import { useLayoutEffect, useState } from 'react';

/** The value `:root` ships, and the fallback if the token is unreadable. */
const DEFAULT_RADIUS_PX = 10;

/**
 * The live `--radius` in CSS pixels, re-read whenever the picker stamps
 * <html>. A custom-property change alone does not re-render React, so the
 * attribute has to be observed.
 */
export function useRadiusPx(): number {
  const [px, setPx] = useState(DEFAULT_RADIUS_PX);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const read = () => {
      const cs = getComputedStyle(root);
      const raw = cs.getPropertyValue('--radius').trim();
      let v = DEFAULT_RADIUS_PX;
      if (raw.endsWith('rem')) v = parseFloat(raw) * (parseFloat(cs.fontSize) || 16);
      else if (raw.endsWith('px')) v = parseFloat(raw);
      setPx(Number.isFinite(v) ? v : DEFAULT_RADIUS_PX);
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ['class', 'data-theme', 'data-accent', 'data-radius', 'style'] });
    return () => mo.disconnect();
  }, []);
  return px;
}

/**
 * A corner may never eat the shape it is rounding.
 *
 * At Pill the token is 16px. A 6px-tall bar with a 16px corner is not a
 * rounded bar, it is a lozenge — and a bar's height IS its value, so the
 * chart stops being readable exactly when the reader asks for softer
 * corners. Half the smaller dimension is the largest radius that still
 * leaves a straight edge to read.
 */
export const clampRadius = (r: number, ...dimensions: number[]): number =>
  Math.max(0, Math.min(r, ...dimensions.map((d) => Math.abs(d) / 2)));
