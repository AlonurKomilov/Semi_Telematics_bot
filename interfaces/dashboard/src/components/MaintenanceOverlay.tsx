// Friendly "updating…" overlay for API restarts.
//
// api/client.ts announces gateway-level failures (502/503/504 — the API is
// restarting behind nginx) via a '4truck:maintenance' event.  This overlay
// then covers the app with a calm "we're updating" card and polls
// /api/health; the moment the API answers, it clears itself — no reload,
// the user resumes exactly where they were.  App-level errors (4xx/500
// JSON) never trigger it.
import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

export default function MaintenanceOverlay() {
  const [visible, setVisible] = useState(false);
  const polling = useRef(false);

  useEffect(() => {
    const onMaintenance = () => {
      setVisible(true);
      if (polling.current) return;
      polling.current = true;
      const poll = async () => {
        try {
          const r = await fetch(`${API_BASE}/health`, { cache: 'no-store', credentials: 'include' });
          if (r.ok) {
            polling.current = false;
            setVisible(false);
            return;
          }
        } catch { /* still down */ }
        setTimeout(poll, 4000);
      };
      setTimeout(poll, 3000);
    };
    window.addEventListener('4truck:maintenance', onMaintenance);
    return () => window.removeEventListener('4truck:maintenance', onMaintenance);
  }, []);

  if (!visible) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="mx-4 max-w-sm rounded-lg border border-border bg-card p-6 text-center shadow-xl">
        <Loader2 size={28} className="mx-auto animate-spin text-primary" />
        <h2 className="mt-4 text-base font-semibold text-foreground">Updating the platform</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          A quick update is being rolled out — this usually takes under a
          minute. We&rsquo;ll reconnect automatically.
        </p>
        <p className="mt-2 text-xs text-muted-foreground/70">
          Taking longer? Please try again in a couple of minutes.
        </p>
      </div>
    </div>
  );
}
