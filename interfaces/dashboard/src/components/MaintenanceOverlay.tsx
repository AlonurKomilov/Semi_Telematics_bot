// Friendly "updating…" overlay for API restarts.
//
// api/client.ts announces gateway-level failures (502/503/504 — the API is
// restarting behind nginx) via a '4truck:maintenance' event.  This overlay
// covers the app with a calm "we're updating" card, polls /api/health, and
// when the API is back it RELOADS the page — by then the app underneath has
// already failed requests (auth check, page data) it will never retry, so
// merely hiding the card would strand the user on a broken screen.  The
// reload lands them on a fully working page.  App-level errors (4xx/500
// JSON) never trigger any of this.
import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

export default function MaintenanceOverlay() {
  const [state, setState] = useState<'hidden' | 'updating' | 'reloading'>('hidden');
  const polling = useRef(false);

  useEffect(() => {
    const onMaintenance = () => {
      setState((s) => (s === 'hidden' ? 'updating' : s));
      if (polling.current) return;
      polling.current = true;
      const poll = async () => {
        try {
          const r = await fetch(`${API_BASE}/health`, { cache: 'no-store', credentials: 'include' });
          if (r.ok) {
            setState('reloading');
            window.location.reload();
            return;
          }
        } catch { /* still down */ }
        setTimeout(poll, 4000);
      };
      setTimeout(poll, 2500);
    };
    window.addEventListener('4truck:maintenance', onMaintenance);
    return () => window.removeEventListener('4truck:maintenance', onMaintenance);
  }, []);

  if (state === 'hidden') return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="mx-4 max-w-sm rounded-lg border border-border bg-card p-6 text-center shadow-xl">
        <Loader2 size={24} className="mx-auto animate-spin text-primary" />
        <h2 className="mt-4 text-base font-semibold text-foreground">
          {state === 'reloading' ? 'Back online' : 'Updating the platform'}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {state === 'reloading'
            ? 'Reloading…'
            : 'A quick update is being rolled out — this usually takes under a minute. We’ll reconnect automatically.'}
        </p>
        {state === 'updating' && (
          <p className="mt-2 text-xs text-muted-foreground/70">
            Taking longer? Please try again in a couple of minutes.
          </p>
        )}
      </div>
    </div>
  );
}
