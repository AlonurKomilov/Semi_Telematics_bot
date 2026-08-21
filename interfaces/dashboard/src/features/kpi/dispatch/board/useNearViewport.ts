/**
 * "Is this element near the viewport yet?" — the board's mount trigger.
 *
 * O-1 (perf audit, owner-approved 2026-08-21): a section's body mounts
 * only when its placeholder comes within ~1.5 screens of the viewport,
 * so opening the board costs the on-screen sections — never the fleet.
 * Built on IntersectionObserver: the browser reports approach as an
 * event; nothing runs per scroll frame.
 *
 * Fires ONCE and never un-fires — a mounted section stays mounted
 * (remount-on-scroll would re-pay the build cost and drop transient
 * state).  Without IntersectionObserver (old engines, jsdom) it falls
 * back to a staggered timer, which reproduces the previous behaviour:
 * everything mounts, spread over frames.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export function useNearViewport(
  /** Stagger position for the no-observer fallback. */
  fallbackIndex: number,
  /** How early to fire, as viewport-heights ahead (default 1.5). */
  ahead = 1.5,
): { near: boolean; ref: (el: HTMLElement | null) => void } {
  const [near, setNear] = useState(false);
  const elRef = useRef<HTMLElement | null>(null);
  const obsRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => () => obsRef.current?.disconnect(), []);

  // Stable while waiting — a per-render identity would make React
  // detach/re-attach the ref (and the observer) on every render.
  const ref = useCallback((el: HTMLElement | null) => {
    elRef.current = el;
    obsRef.current?.disconnect();
    obsRef.current = null;
    if (el == null || near) return;
    if (typeof IntersectionObserver === 'undefined') {
      // Fallback = the previous timer stagger, ~two sections a frame.
      setTimeout(() => setNear(true), Math.floor(fallbackIndex / 2) * 16);
      return;
    }
    const obs = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        obs.disconnect();
        obsRef.current = null;
        setNear(true);
      }
    }, { rootMargin: `${Math.round(ahead * 100)}% 0px` });
    obs.observe(el);
    obsRef.current = obs;
  }, [near, fallbackIndex, ahead]);

  return { near, ref };
}
