import { NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import type { Permissions } from '../types';

interface NavItem {
  label: string;
  path: string;
  icon: string;
  permission: string | string[] | null;
}

interface NavGroup {
  title: string | null;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: null,
    items: [
      { label: 'Overview',       path: '/',                      icon: '📊', permission: null },
      { label: 'Vehicles',       path: '/fleet/vehicles',        icon: '🚛', permission: ['can_truck_all', 'can_truck_own'] },
      { label: 'Alerts',         path: '/dispatch/alerts',       icon: '🔔', permission: ['can_alerts_all', 'can_alerts_own'] },
      { label: 'AI Chat',        path: '/ai/chat',               icon: '🤖', permission: null },
      { label: 'Reports',        path: '/reports',               icon: '📄', permission: null },
      { label: 'Subscriptions',  path: '/reports/subscriptions', icon: '📬', permission: null },
      { label: 'Geofences',      path: '/dispatch/geofences',    icon: '📍', permission: ['can_geofence_all', 'can_geofence_own'] },
      { label: 'Knowledge Base', path: '/knowledge',             icon: '📚', permission: null },
      { label: 'Settings',       path: '/admin/settings',        icon: '⚙️', permission: null },
    ],
  },
  {
    title: 'Fleet',
    items: [
      { label: 'Weather',     path: '/fleet/weather', icon: '🌡️', permission: ['can_truck_all', 'can_truck_own'] },
      { label: 'Maintenance', path: '/maintenance',   icon: '🔧', permission: ['can_maintenance_all', 'can_maintenance_own'] },
    ],
  },
  {
    title: 'Dispatch',
    items: [
      { label: 'Live Map', path: '/fleet/map',       icon: '🗺️', permission: ['can_location_map', 'can_location_own'] },
      { label: 'Routes',   path: '/dispatch/routes', icon: '🛣️', permission: ['can_route_all', 'can_route_own'] },
    ],
  },
  {
    title: 'Safety & Compliance',
    items: [
      { label: 'Scorecards',  path: '/safety/scorecards', icon: '🏆', permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { label: 'Events',      path: '/safety/events',     icon: '⚠️', permission: ['can_events_all', 'can_events_own'] },
      { label: 'Cameras',     path: '/safety/cameras',    icon: '📷', permission: ['can_faults'] },
      { label: 'Parking',     path: '/safety/parking',    icon: '🅿️', permission: ['can_alerts_all', 'can_alerts_own'] },
      { label: 'Fuel Costs',  path: '/costs/fuel',        icon: '⛽', permission: ['can_fuel_cost'] },
      { label: 'Cost / Mile', path: '/costs/cpm',         icon: '💰', permission: ['can_cost_per_mile'] },
    ],
  },
  {
    title: 'Admin',
    items: [
      { label: 'Team',        path: '/admin/users',       icon: '👥', permission: ['can_manage_users'] },
      { label: 'Permissions', path: '/admin/permissions', icon: '🔐', permission: ['can_manage_account'] },
      { label: 'Companies',   path: '/admin/companies',   icon: '🏢', permission: ['can_manage_companies'] },
      { label: 'Invites',     path: '/admin/invites',     icon: '🔗', permission: ['can_invite'] },
      { label: 'Audit Log',   path: '/admin/audit',       icon: '📋', permission: ['can_manage_users'] },
      { label: 'Schedules',   path: '/admin/schedules',   icon: '🕐', permission: ['can_manage_account'] },
    ],
  },
];

export default function Sidebar() {
  const { user } = useAuth();
  const perms = user?.permissions ?? ({} as Partial<Permissions>);

  const hasAny = (...flags: string[]) =>
    flags.some((f) => !!perms[f as keyof Permissions]);

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (!item.permission) return true;
      return hasAny(...(Array.isArray(item.permission) ? item.permission : [item.permission]));
    });

  return (
    <aside className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 overflow-y-auto">
      <div className="h-14 flex items-center px-4 border-b border-gray-800">
        <span className="text-lg font-bold">4truck</span>
      </div>

      <nav className="flex-1 py-2">
        {NAV_GROUPS.map((group) => {
          const items = filterItems(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.title ?? '_top'}>
              {group.title && (
                <div className="px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
                  {group.title}
                </div>
              )}
              {items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      isActive
                        ? 'bg-blue-600/20 text-blue-400 border-r-2 border-blue-500'
                        : 'text-gray-400 hover:text-white hover:bg-gray-800'
                    }`
                  }
                >
                  <span className="text-base">{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
