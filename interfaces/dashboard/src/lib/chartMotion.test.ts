/**
 * The one animator the Motion axis and the reduced-motion floor both
 * miss.
 *
 * Recharts interpolates in JavaScript, so there is no CSS animation for
 * the floor to end and no duration for `--motion-scale` to multiply.
 * Everything below is about a value arriving as a NUMBER instead of as a
 * class — which is the whole reason this module exists, and the reason
 * its failures are silent: a chart that animates for 1500ms while the
 * app around it is on Snappy looks like the chart is slow, and a chart
 * that animates at all for somebody who asked for no motion looks like
 * the setting does not work.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useChartMotionMs, chartMotion, CHART_MOTION_MS } from './chartMotion';

/** What the document says, for one test. */
function stub({ scale, reduced }: { scale?: string; reduced?: boolean }) {
  const root = document.documentElement;
  if (scale === undefined) root.style.removeProperty('--motion-scale');
  else root.style.setProperty('--motion-scale', scale);
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('reduced-motion') ? !!reduced : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

describe('a chart duration is the Motion axis, in milliseconds', () => {
  beforeEach(() => { document.documentElement.style.removeProperty('--motion-scale'); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('is the default when nothing has been chosen', () => {
    stub({});
    expect(renderHook(() => useChartMotionMs()).result.current).toBe(CHART_MOTION_MS);
  });

  it('and it is far shorter than the library default it replaces', () => {
    // Recharts ships 1500ms. That is longer than it takes to read the
    // chart: a bar still growing when the eye has arrived reads as the
    // app being slow, not as the chart being alive.
    expect(CHART_MOTION_MS).toBeLessThan(1000);
  });

  it('lengthens on Calm and shortens on Snappy', () => {
    stub({ scale: '1.6' });
    expect(renderHook(() => useChartMotionMs()).result.current)
      .toBe(Math.round(CHART_MOTION_MS * 1.6));
    stub({ scale: '0.6' });
    expect(renderHook(() => useChartMotionMs()).result.current)
      .toBe(Math.round(CHART_MOTION_MS * 0.6));
  });

  it('is ZERO for somebody who asked for no motion', () => {
    // The floor in `index.css` is the only `!important` in the
    // stylesheet and it reaches every CSS animation in the product. It
    // cannot reach this one, so the promise has to be kept here or it is
    // not kept: 22 series in the product would keep animating for a
    // person whose operating system says not to.
    stub({ scale: '1.6', reduced: true });
    expect(renderHook(() => useChartMotionMs()).result.current).toBe(0);
  });

  it('and at zero the tween is switched OFF, not merely made instant', () => {
    // Recharts treats `animationDuration: 0` as "animate over 0ms": it
    // still mounts the tween and still schedules a frame. `off` is off.
    expect(chartMotion(0)).toEqual({ isAnimationActive: false });
    expect(chartMotion(600)).toEqual({ animationDuration: 600 });
  });

  it('survives a token it cannot read', () => {
    // Best-effort, like everything else on this path: a chart that
    // throws is a page that stops.
    stub({ scale: 'not-a-number' });
    expect(renderHook(() => useChartMotionMs()).result.current).toBe(CHART_MOTION_MS);
  });
});
