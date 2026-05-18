import { useEffect, useState } from 'react';

/**
 * Track the *stage* of a long-running fetch so the UI can give the user
 * progressively louder feedback:
 *
 *   - `'fast'`    — within the normal latency budget, render a quiet
 *                   skeleton with the default "Loading…" pill.
 *   - `'slow'`    — past `slowAfterMs` (default 8s).  Surface a "Still
 *                   loading…" message so users know the page isn't
 *                   frozen.  Common cause: warehouse cache miss → live
 *                   Samsara fallback.
 *   - `'timeout'` — past `timeoutAfterMs` (default 30s).  The page
 *                   should switch to an `ErrorState` with a Retry
 *                   button instead of leaving the skeleton up forever.
 *
 * Resets to `'fast'` whenever `loading` flips false (e.g. data arrived).
 */
export function useLoadingStage(
  loading: boolean,
  opts: { slowAfterMs?: number; timeoutAfterMs?: number } = {},
): 'fast' | 'slow' | 'timeout' {
  const slowAfterMs = opts.slowAfterMs ?? 8000;
  const timeoutAfterMs = opts.timeoutAfterMs ?? 30000;
  const [stage, setStage] = useState<'fast' | 'slow' | 'timeout'>('fast');

  useEffect(() => {
    if (!loading) {
      setStage('fast');
      return;
    }
    setStage('fast');
    const slowTimer = setTimeout(() => setStage('slow'), slowAfterMs);
    const timeoutTimer = setTimeout(() => setStage('timeout'), timeoutAfterMs);
    return () => {
      clearTimeout(slowTimer);
      clearTimeout(timeoutTimer);
    };
  }, [loading, slowAfterMs, timeoutAfterMs]);

  return stage;
}
