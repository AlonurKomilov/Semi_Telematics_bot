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
        setPulling(Math.min(dy, threshold * 1.5));
      }
    };
    const onEnd = async () => {
      if (startY.current === null) { setPulling(0); return; }
      const final = pulling;
      startY.current = null;
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
  }, [enabled, threshold, pulling]);

  return { ref, pulling, refreshing };
}
