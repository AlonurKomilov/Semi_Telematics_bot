/**
 * Alerts area tabs — the SINGLE gate's sub-navigation (docs §13).
 *
 * Alerts is the mother surface: the operational Board and the personal
 * Notification-preferences live under it as tabs, reached from the one
 * topbar bell (its dropdown gear deep-links the Preferences tab).  Both
 * the board page and the preferences page render this strip, so switching
 * keeps a shared skeleton instead of feeling like two separate pages.
 *
 * The Board tab is gated on the alerts view permission (like the bell);
 * Preferences is self-scoped, so every role that can reach this area sees
 * it.
 */
import { NavLink } from 'react-router-dom';
import { useViewPermissions } from '../../hooks/useViewPermissions';

const P_ALERTS = ['can_alerts_all', 'can_alerts_vehicle'];

function Tab({ to, end, children }: {
  to: string; end?: boolean; children: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
          isActive
            ? 'border-primary text-foreground'
            : 'border-transparent text-muted-foreground hover:text-foreground'
        }`
      }
    >
      {children}
    </NavLink>
  );
}

export function AlertsTabs() {
  const { hasAny } = useViewPermissions();
  const canBoard = hasAny(...P_ALERTS);
  // Group delivery (where account alerts route) — owner/admin, or a role
  // manager (who manages only their own row).  Personal DMs stay on the
  // Preferences tab; this is the admin/group side.
  const canDelivery = hasAny('can_manage_account', 'can_manage_role_bot');
  return (
    <div className="flex items-center gap-1 border-b border-border mb-4">
      {canBoard && <Tab to="/alerts" end>Board</Tab>}
      <Tab to="/alerts/preferences">Notification preferences</Tab>
      {canDelivery && <Tab to="/alerts/group-delivery">Group delivery</Tab>}
    </div>
  );
}

export default AlertsTabs;
