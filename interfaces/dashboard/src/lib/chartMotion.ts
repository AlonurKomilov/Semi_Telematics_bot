/**
 * The Motion axis, for the one animator CSS cannot reach.
 *
 * Every transition in the app rides `--motion-scale`, and the
 * reduced-motion floor ends every CSS animation with the only
 * `!important` in the stylesheet. Recharts obeys NEITHER: it animates in
 * JavaScript (react-smooth interpolates between frames), so there is no
 * CSS animation for the floor to stop and no duration for the multiplier
 * to multiply. Twenty-two series in this product animate for 1500ms —
 * recharts' own default — while the controls around them shorten to 0.6×
 * on Snappy, and they keep doing it for somebody who has asked their
 * operating system for no motion at all.
 *
 * This is the Motion twin of `lib/radius.ts` on Corners and
 * `lib/chartText.ts` on Size: the axis, read live, handed to a library
 * that only takes numbers.
 */
import { useLayoutEffect, useState } from 'react';

/**
 * What a chart takes to draw itself, before the axis is applied.
 *
 * Not recharts' 1500ms. That number was chosen for a marketing page, and
 * it is longer than the time it takes to read the chart — at a desk, a
 * bar still growing when the eye has already reached it reads as the app
 * being slow rather than as the chart being alive.
 */
export const CHART_MOTION_MS = 600;

/** `--motion-scale` when the token is unreadable. */
const DEFAULT_SCALE = 1;

/**
 * The live chart duration in milliseconds, scaled by the Motion axis and
 * ZERO under `prefers-reduced-motion`.
 *
 * Zero rather than 0.01ms: the CSS floor uses 0.01ms because a duration
 * of exactly 0 cancels the `animationend` event some code waits for.
 * Nothing waits on a recharts tween, so zero is the honest value — and
 * recharts skips the interpolation entirely, which is the point.
 */
export function useChartMotionMs(base: number = CHART_MOTION_MS): number {
  const [ms, setMs] = useState(base);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const reduced = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null;
    const read = () => {
      if (reduced?.matches) { setMs(0); return; }
      const raw = getComputedStyle(root).getPropertyValue('--motion-scale').trim();
      const scale = parseFloat(raw);
      setMs(Math.round(base * (Number.isFinite(scale) ? scale : DEFAULT_SCALE)));
    };
    read();
    // A custom-property change alone does not re-render React, so the
    // attribute the picker stamps has to be observed — the same bargain
    // `useRadiusPx` makes, for the same reason.
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ['data-motion', 'data-mod', 'style', 'class'] });
    reduced?.addEventListener?.('change', read);
    return () => {
      mo.disconnect();
      reduced?.removeEventListener?.('change', read);
    };
  }, [base]);
  return ms;
}

/**
 * Everything a recharts series needs to obey the axis, in one spread.
 *
 * `isAnimationActive` as well as the duration, because recharts treats a
 * duration of 0 as "animate over 0ms" — it still mounts the tween, still
 * schedules a frame, and on a 5,000-point series that is work nobody
 * asked for. Off is off.
 */
export const chartMotion = (ms: number) =>
  (ms > 0 ? { animationDuration: ms } : { isAnimationActive: false as const });
