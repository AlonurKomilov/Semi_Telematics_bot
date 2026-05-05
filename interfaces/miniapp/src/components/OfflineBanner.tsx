import { useEffect, useState } from 'react';
import { getLastFetchAt } from '../api/client';

export function OfflineBanner() {
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  );
  // Captured once when we go offline so the label stays stable.
  const [cachedAt, setCachedAt] = useState<number | null>(null);

  useEffect(() => {
    function on() {
      setOnline(true);
      setCachedAt(null);
    }
    function off() {
      setOnline(false);
      setCachedAt(getLastFetchAt());
    }
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);

  if (online) return null;

  const timeLabel = cachedAt
    ? new Date(cachedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null;

  return (
    <div
      className="offline-banner"
      role="status"
      aria-live="polite"
    >
      <span aria-hidden>📡</span>
      <span>
        {timeLabel ? `Offline — cached data as of ${timeLabel}` : 'Offline — showing cached data'}
      </span>
    </div>
  );
}
