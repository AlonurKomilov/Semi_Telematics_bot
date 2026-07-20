/**
 * Topbar Alerts bell — Alerts as a cross-cutting monitoring SERVICE in
 * the topbar cluster (beside the AI + theme icons), not a sidebar
 * feature row.  Click navigates to the /alerts page; a badge shows the
 * pending (unacknowledged) count.
 *
 * The count reuses useShellStats() — the SAME shared query the persona
 * heroes read (one deduped /overview/stats fetch, 60s stale), so the
 * badge can never drift from the heroes and adds no extra request.
 *
 * Gated on the alerts permission (view-scoped) like the sidebar item
 * was, and hidden on /alerts itself (you're already there).
 */
import { useLocation, useNavigate } from 'react-router-dom';
import { Bell } from 'lucide-react';
import { useShellStats } from '../../shells/heroes/useShellStats';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { Tip } from '../../components/tooltip';

const P_ALERTS = ['can_alerts_all', 'can_alerts_vehicle'];

export function AlertsLauncher() {
  const location = useLocation();
  const { hasAny } = useViewPermissions();
  // Gate BEFORE the stats query so users without alerts access (or those
  // already on /alerts) never trigger the /overview/stats fetch.
  if (location.pathname.startsWith('/alerts') || !hasAny(...P_ALERTS)) return null;
  return <AlertsBell />;
}

function AlertsBell() {
  const navigate = useNavigate();
  const { data } = useShellStats();
  const pending = data?.pending_alerts ?? 0;
  const badge = pending > 99 ? '99+' : String(pending);

  return (
    <Tip label={pending > 0 ? `Alerts — ${pending} pending` : 'Alerts'}>
      <button
        onClick={() => navigate('/alerts')}
        aria-label={pending > 0 ? `Alerts, ${pending} pending` : 'Alerts'}
        className="relative inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
      >
        <Bell size={18} aria-hidden />
        {pending > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 min-w-4 h-4 px-1 inline-flex items-center justify-center rounded-full bg-danger text-white text-3xs font-semibold tabular-nums"
            aria-hidden
          >
            {badge}
          </span>
        )}
      </button>
    </Tip>
  );
}

export default AlertsLauncher;
