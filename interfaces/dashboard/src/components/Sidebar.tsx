import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Thermometer, Wrench, Map, Route, Trophy, AlertTriangle,
  Camera, ParkingSquare, Fuel, DollarSign, Users, Shield, Building2,
  Link, ClipboardList, Clock, CreditCard, type LucideIcon,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import type { Permissions } from '../types';

interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
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
      { label: 'Overview',             path: '/',                      icon: LayoutDashboard, permission: null },
      { label: 'Vehicles',             path: '/fleet/vehicles',        icon: Truck,           permission: ['can_truck_all', 'can_truck_own'] },
      { label: 'Alerts',               path: '/dispatch/alerts',       icon: Bell,            permission: ['can_alerts_all', 'can_alerts_own'] },
      { label: 'AI Assistant',         path: '/ai/chat',               icon: Bot,             permission: 'can_faults' },
      { label: 'Reports',              path: '/reports',               icon: FileText,        permission: null },
      { label: 'Report Subscriptions', path: '/reports/subscriptions', icon: Mail,            permission: null },
      { label: 'Geofences',            path: '/dispatch/geofences',    icon: MapPin,          permission: ['can_geofence_all', 'can_geofence_own'] },
      { label: 'Knowledge Base',       path: '/knowledge',             icon: BookOpen,        permission: null },
    ],
  },
  {
    title: 'Fleet',
    items: [
      { label: 'Weather',     path: '/fleet/weather', icon: Thermometer, permission: ['can_truck_all', 'can_truck_own'] },
      { label: 'Maintenance', path: '/maintenance',   icon: Wrench,      permission: ['can_maintenance_all', 'can_maintenance_own'] },
    ],
  },
  {
    title: 'Dispatch',
    items: [
      { label: 'Live Map', path: '/fleet/map',       icon: Map,   permission: ['can_location_map', 'can_location_own'] },
      { label: 'Routes',   path: '/dispatch/routes', icon: Route, permission: ['can_route_all', 'can_route_own'] },
    ],
  },
  {
    title: 'Safety & Compliance',
    items: [
      { label: 'Driver Scorecards', path: '/safety/scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { label: 'Safety Events',     path: '/safety/events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { label: 'Cameras',           path: '/safety/cameras',    icon: Camera,        permission: ['can_faults'] },
      { label: 'Parking',           path: '/safety/parking',    icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
      { label: 'Fuel Costs',        path: '/costs/fuel',        icon: Fuel,          permission: ['can_fuel_cost'] },
      { label: 'Cost per Mile',     path: '/costs/cpm',         icon: DollarSign,    permission: ['can_cost_per_mile'] },
    ],
  },
  {
    title: 'Admin',
    items: [
      { label: 'Team Management',  path: '/admin/users',       icon: Users,         permission: ['can_manage_users'] },
      { label: 'Role Permissions', path: '/admin/permissions', icon: Shield,        permission: ['can_manage_account'] },
      { label: 'Companies',        path: '/admin/companies',   icon: Building2,     permission: ['can_manage_companies'] },
      { label: 'Invites',          path: '/admin/invites',     icon: Link,          permission: ['can_invite'] },
      { label: 'Audit Log',        path: '/admin/audit',       icon: ClipboardList, permission: ['can_manage_users'] },
      { label: 'Working Hours',    path: '/admin/work-hours',  icon: Clock,         permission: ['can_manage_account'] },
      { label: 'Billing & Plan',   path: '/admin/billing',     icon: CreditCard,    permission: ['can_manage_billing'] },
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
    <aside className="w-56 bg-card border-r border-border flex flex-col shrink-0 h-screen">
      {/* Logo — always visible, never scrolls */}
      <div className="h-14 flex items-center px-4 border-b border-border shrink-0 gap-2">
        <Truck size={20} className="text-primary shrink-0" />
        <span className="text-lg font-bold text-foreground">4truck</span>
      </div>

      {/* Nav — scrolls independently */}
      <nav className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {NAV_GROUPS.map((group) => {
          const items = filterItems(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.title ?? '_top'}>
              {group.title && (
                <div className="px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  {group.title}
                </div>
              )}
              {items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === '/'}
                    className={({ isActive }) =>
                      `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                        isActive
                          ? 'bg-primary/15 text-primary border-r-2 border-primary'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                      }`
                    }
                  >
                    <Icon size={16} className="shrink-0" />
                    {item.label}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
