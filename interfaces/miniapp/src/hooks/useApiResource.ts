// Refetch a value whenever a tab becomes active, plus retry+timeout policy.

import { useEffect, useRef, useState } from 'react';
import { apiJSON } from '../api/client';

interface Opts {
  active?: boolean; // refetch when transitions false→true
  refreshKey?: number | string; // bump to force refresh
  retries?: number;
  retryDelayMs?: number;
}

interface State<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refreshing: boolean;
  reload(): Promise<void>;
}

export function useApiResource<T>(path: string, opts: Opts = {}): State<T> {
  const { active = true, refreshKey, retries = 1, retryDelayMs = 1500 } = opts;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const wasActive = useRef(active);

  async function run(silent = false): Promise<void> {
    if (!silent) setLoading(true); else setRefreshing(true);
    setError(null);
    let lastErr: unknown = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const json = await apiJSON<T>(path);
        setData(json);
        setError(null);
        if (!silent) setLoading(false); else setRefreshing(false);
        return;
      } catch (e) {
        lastErr = e;
        if (attempt < retries) {
          await new Promise(r => setTimeout(r, retryDelayMs));
        }
      }
    }
    setError(lastErr instanceof Error ? lastErr.message : 'Failed to load');
    if (!silent) setLoading(false); else setRefreshing(false);
  }

  // Initial load + manual refreshKey bumps.
  useEffect(() => {
    run(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, refreshKey]);

  // Refetch when tab becomes active.
  useEffect(() => {
    if (active && !wasActive.current) {
      run(true);
    }
    wasActive.current = active;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return { data, error, loading, refreshing, reload: () => run(true) };
}
