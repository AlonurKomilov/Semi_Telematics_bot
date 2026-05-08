import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Thermometer, Wrench, Map, Route, Trophy, AlertTriangle,
  Camera, ParkingSquare, Fuel, DollarSign, Users, Shield, Building2,
  Link, ClipboardList, Clock, CreditCard, GraduationCap, type LucideIcon,
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
      { label: 'Overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { label: 'AI Assistant', path: '/ai/chat', icon: Bot,             permission: 'can_faults' },
    ],
  },
  {
    title: 'Fleet',
    items: [
      { label: 'Live Map',    path: '/fleet/map',       icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { label: 'Vehicles',    path: '/fleet/vehicles',  icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
      { label: 'Routes',      path: '/fleet/routes',    icon: Route,         permission: ['can_route_all', 'can_route_own'] },
      { label: 'Geofences',   path: '/fleet/geofences', icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { label: 'Weather',     path: '/fleet/weather',   icon: Thermometer,   permission: ['can_vehicle_all', 'can_vehicle_own'] },
      { label: 'Maintenance', path: '/maintenance',     icon: Wrench,        permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { label: 'Parking',     path: '/fleet/parking',   icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    title: 'Safety',
    items: [
      { label: 'Driver Scorecards', path: '/safety/scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { label: 'Safety Events',     path: '/safety/events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { label: 'Cameras',           path: '/safety/cameras',    icon: Camera,        permission: ['can_faults'] },
      { label: 'Alerts',            path: '/safety/alerts',     icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    title: 'Reports',
    items: [
      { label: 'Reports',       path: '/reports',               icon: FileText,   permission: null },
      { label: 'Risk Summary',  path: '/reports/risk-summary',  icon: FileText,   permission: ['can_risk_report_all', 'can_risk_report_own'] },
      { label: 'Subscriptions', path: '/reports/subscriptions', icon: Mail,       permission: null },
      { label: 'Fuel Costs',    path: '/costs/fuel',            icon: Fuel,       permission: ['can_fuel_cost'] },
      { label: 'Cost per Mile', path: '/costs/cpm',             icon: DollarSign, permission: ['can_cost_per_mile'] },
    ],
  },
  {
    title: 'Workforce',
    items: [
      { label: 'Coaching',        path: '/coaching',         icon: GraduationCap, permission: ['can_coaching_admin'] },
      { label: 'Payroll',         path: '/payroll',          icon: DollarSign,    permission: ['can_payroll_admin'] },
      { label: 'Working Hours',   path: '/admin/work-hours', icon: Clock,         permission: ['can_manage_account'] },
      { label: 'Team Management', path: '/admin/users',      icon: Users,         permission: ['can_manage_users'] },
      { label: 'Invites',         path: '/admin/invites',    icon: Link,          permission: ['can_invite'] },
    ],
  },
  {
    title: 'Admin',
    items: [
      { label: 'Companies',        path: '/admin/companies',       icon: Building2,     permission: ['can_manage_companies'] },
      { label: 'Role Permissions', path: '/admin/permissions',     icon: Shield,        permission: ['can_manage_account'] },
      { label: 'Scorecard Rules',  path: '/admin/scorecard-rules', icon: Trophy,        permission: ['can_manage_account'] },
      { label: 'Billing & Plan',   path: '/admin/billing',         icon: CreditCard,    permission: ['can_manage_billing'] },
      { label: 'Audit Log',        path: '/admin/audit',           icon: ClipboardList, permission: ['can_manage_users'] },
    ],
  },
  {
    title: null,
    items: [
      { label: 'Knowledge Base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
];

export default function Sidebar() {
  const { user } = useAuth();
  const perms = user?.permissions ?? ({} as Partial<Permissions>);

  const hasAny = (...flags: string[]) =>
    flags.some((f) => !!perms[f as keyof Permissions]);

  const isFeatureVisible = (item: NavItem) => {
    if (item.path === '/payroll') return user?.payroll_enabled !== false;
    if (item.path === '/coaching') return user?.coaching_enabled !== false;
    return true;
  };

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (!isFeatureVisible(item)) return false;
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
