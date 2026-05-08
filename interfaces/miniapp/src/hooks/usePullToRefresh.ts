// Pull-to-refresh gesture hook.  Returns a ref to attach to the scroll
// container and a `pulling` distance for visual feedback, plus a `refreshing`
// flag.  When the user releases past the threshold, `onRefresh` is called.

import { useEffect, useRef, useState } from 'react';

interface Opts {
  onRefresh: () => Promise<void> | void;
  threshold?: number; // px to drag before triggering
  enabled?: boolean;
}

export function usePullToRefresh<T extends HTMLElement>({ onRefresh, threshold = 64, enabled = true }: Opts) {
  const ref = useRef<T | null>(null);
  const [pulling, setPulling] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const startY = useRef<number | null>(null);
  // Live mirror of `pulling` so onEnd reads the current value without
  // having to be a dep of the touch-listener effect.  Without this mirror,
  // every onMove → setPulling re-runs the effect mid-gesture and tears
  // the listeners down/up ~60×/s, which made PTR fire randomly.
  const pullingRef = useRef(0);
  const cbRef = useRef(onRefresh);
  cbRef.current = onRefresh;

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;

    const onStart = (e: TouchEvent) => {
      if (el.scrollTop > 0) return;
      startY.current = e.touches[0].clientY;
    };
    const onMove = (e: TouchEvent) => {
      if (startY.current === null) return;
      const dy = e.touches[0].clientY - startY.current;
      if (dy > 0) {
        const next = Math.min(dy, threshold * 1.5);
        pullingRef.current = next;
        setPulling(next);
      }
    };
    const onEnd = async () => {
      if (startY.current === null) { pullingRef.current = 0; setPulling(0); return; }
      const final = pullingRef.current;
      startY.current = null;
      pullingRef.current = 0;
      setPulling(0);
      if (final >= threshold) {
        setRefreshing(true);
        try { await cbRef.current(); }
        finally { setRefreshing(false); }
      }
    };

    el.addEventListener('touchstart', onStart, { passive: true });
    el.addEventListener('touchmove', onMove, { passive: true });
    el.addEventListener('touchend', onEnd);
    el.addEventListener('touchcancel', onEnd);
    return () => {
      el.removeEventListener('touchstart', onStart);
      el.removeEventListener('touchmove', onMove);
      el.removeEventListener('touchend', onEnd);
      el.removeEventListener('touchcancel', onEnd);
    };
  }, [enabled, threshold]);

  return { ref, pulling, refreshing };
}
