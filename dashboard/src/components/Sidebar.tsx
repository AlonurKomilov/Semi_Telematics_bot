import { NavLink } from 'react-router-dom';
import { usePermissions } from '../hooks/usePermissions';

interface NavItem {
  label: string;
  path: string;
  icon: string;
  permission: string | string[] | null;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Overview',     path: '/',              icon: '📊', permission: null },
  { label: 'Vehicles',     path: '/fleet/vehicles', icon: '🚛', permission: ['can_truck_all', 'can_truck_own'] },
  { label: 'Live Map',     path: '/fleet/map',      icon: '🗺️', permission: ['can_location_map', 'can_location_own'] },
  { label: 'Alerts',       path: '/dispatch/alerts', icon: '🔔', permission: ['can_alerts_all', 'can_alerts_own'] },
  { label: 'Geofences',    path: '/dispatch/geofences', icon: '📍', permission: ['can_geofence_all', 'can_geofence_own'] },
  { label: 'Routes',       path: '/dispatch/routes', icon: '🛣️', permission: ['can_route_all', 'can_route_own'] },
  { label: 'Scorecards',   path: '/safety/scorecards', icon: '🏆', permission: ['can_scorecard_all', 'can_scorecard_own'] },
  { label: 'Safety Events', path: '/safety/events',  icon: '⚠️', permission: ['can_events_all', 'can_events_own'] },
  { label: 'Cameras',      path: '/safety/cameras',  icon: '📷', permission: ['can_faults'] },
  { label: 'Reports',      path: '/reports',         icon: '📄', permission: ['can_faults'] },
  { label: 'Fuel Costs',   path: '/costs/fuel',      icon: '⛽', permission: ['can_fuel_cost'] },
  { label: 'Cost / Mile',  path: '/costs/cpm',       icon: '💰', permission: ['can_cost_per_mile'] },
  { label: 'Maintenance',  path: '/maintenance',     icon: '🔧', permission: ['can_maintenance_all', 'can_maintenance_own'] },
  { label: 'Team',         path: '/admin/users',     icon: '👥', permission: ['can_manage_users'] },
  { label: 'Companies',    path: '/admin/companies', icon: '🏢', permission: ['can_manage_companies'] },
  { label: 'Audit Log',    path: '/admin/audit',     icon: '📋', permission: ['can_manage_users'] },
  { label: 'Settings',     path: '/admin/settings',  icon: '⚙️', permission: ['can_manage_account'] },
];

export default function Sidebar() {
  const { hasAny } = usePermissions();

  const visible = NAV_ITEMS.filter((item) => {
    if (!item.permission) return true;
    return hasAny(...(Array.isArray(item.permission) ? item.permission : [item.permission]));
  });

  return (
    <aside className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 overflow-y-auto">
      <div className="h-14 flex items-center px-4 border-b border-gray-800">
        <span className="text-lg font-bold">🚛 4truck</span>
      </div>
      <nav className="flex-1 py-2">
        {visible.map((item) => (
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
      </nav>
    </aside>
  );
}
