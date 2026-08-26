/**
 * Alerts area tabs — the operational Board, a person's own Triggers, and
 * the admin Group-delivery routing.  Personal notification PREFERENCES no longer live here: they
 * moved to their own top-level door (/notifications/preferences, reached
 * from the topbar Notifications bell's gear), because notifications are a
 * cross-source personal concern, not an Alerts sub-feature.
 *
 * The Board tab is gated on the alerts view permission (like the bell);
 * Group delivery is owner/admin (or a role manager for their own row).
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
      {/* No gate, and that is not an oversight: a trigger watches only
          the vehicles its owner can already see and reaches nobody else,
          so there is no permission to check.  It also has to stay
          reachable for people who cannot see the Board — they could set
          one from notification preferences before it moved here. */}
      <Tab to="/alerts/triggers">Triggers</Tab>
      {canDelivery && <Tab to="/alerts/group-delivery">Group delivery</Tab>}
    </div>
  );
}

export default AlertsTabs;
