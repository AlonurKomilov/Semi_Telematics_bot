/**
 * Topbar Alerts bell — Alerts as a cross-cutting monitoring SERVICE in
 * the topbar cluster (beside the AI + theme icons), not a sidebar
 * feature row.  Clicking opens the notification-centre DROPDOWN (a quick
 * recent-alerts glance); the full board + the preferences gear live one
 * click away inside it.  This is the SINGLE gate for alerts + prefs.
 *
 * The badge count reuses useShellStats() — the SAME shared query the
 * persona heroes read (one deduped /overview/stats fetch, 60s stale), so
 * the badge can never drift from the heroes and adds no extra request.
 * The recent-feed itself is fetched lazily, only while the dropdown is
 * open (see useRecentAlerts) — the closed bell costs nothing.
 *
 * Gated on the alerts permission (view-scoped) like the sidebar item was.
 */
import { useState } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Bell } from 'lucide-react';
import { useShellStats } from '../../shells/heroes/useShellStats';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { NotificationsPanel } from './NotificationsPanel';
import { useInboxUnread } from './useInbox';

const P_ALERTS = ['can_alerts_all', 'can_alerts_vehicle'];

export function AlertsLauncher() {
  const { hasAny } = useViewPermissions();
  // The bell is the universal Notifications door — every authenticated user
  // gets it (even vehicle-less roles like recruiter/HR who have no alerts,
  // so they can still reach their notification preferences).  Its alert
  // GLANCE stays permission-scoped inside the panel.
  return <AlertsBell canAlerts={hasAny(...P_ALERTS)}
                     canApplications={hasAny('can_manage_applications')} />;
}

function AlertsBell(
  { canAlerts, canApplications }:
  { canAlerts: boolean; canApplications: boolean },
) {
  const [open, setOpen] = useState(false);
  // Only fetch the stats when they'd mean something — a no-alerts role's
  // alert count is forced to 0, so don't pay for the query.
  const { data } = useShellStats(canAlerts);
  // Badge = pending alerts + unread inbox notices, summed CLIENT-side —
  // the two stores stay separate (alerts keep ack semantics; the inbox
  // keeps read semantics) and the bell just adds the numbers.
  const inboxUnread = useInboxUnread(true);
  const pending = (canAlerts ? (data?.pending_alerts ?? 0) : 0) + inboxUnread;
  const badge = pending > 99 ? '99+' : String(pending);
  const label = pending > 0 ? `Notifications, ${pending} pending` : 'Notifications';

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger
        aria-label={label}
        className={`relative inline-flex size-8 items-center justify-center rounded-md transition-colors ${
          open
            ? 'bg-primary/15 text-primary'
            : 'text-muted-foreground hover:bg-muted hover:text-foreground'
        }`}
      >
        <Bell className="size-4.5" aria-hidden />
        {pending > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 min-w-4 h-4 px-1 inline-flex items-center justify-center rounded-full bg-danger text-danger-foreground text-2xs font-semibold tabular-nums"
            aria-hidden
          >
            {badge}
          </span>
        )}
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          side="bottom"
          align="end"
          sideOffset={8}
          className="z-50 outline-none"
        >
          <PopoverPrimitive.Popup className="w-80 bg-popover text-popover-foreground border border-border rounded-lg shadow-lg overflow-hidden">
            <NotificationsPanel onClose={() => setOpen(false)} canAlerts={canAlerts}
                                canApplications={canApplications} />
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

export default AlertsLauncher;
