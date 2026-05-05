import { useEffect, useState } from 'react';

/**
 * Returns a value that only updates after `delayMs` of stillness.
 *
 * Useful for filter/search inputs where running an expensive filter on every
 * keystroke causes typing jank.  Pass the raw input value in, render with the
 * returned debounced value.
 */
export function useDebouncedValue<T>(value: T, delayMs = 200): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
