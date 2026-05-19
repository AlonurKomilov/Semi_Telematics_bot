import { NavLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Wrench, Map, Route, Trophy, AlertTriangle,
  Camera, ParkingSquare, Fuel, DollarSign, Users, Shield, Building2,
  Link, ClipboardList, Clock, CreditCard, GraduationCap, Receipt,
  TrendingUp, Cloud, IdCard, Settings as SettingsIcon,
  type LucideIcon,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useRoleView } from '../context/RoleViewContext';
import { PersonaSelector } from './PersonaSelector';
import type { Permissions } from '../types';

// NavItem labels are i18n keys looked up at render time with t().
// titleKey is null when the group has no header (top + tail groups).
interface NavItem {
  labelKey: string;
  path: string;
  icon: LucideIcon;
  permission: string | string[] | null;
}

interface NavGroup {
  titleKey: string | null;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.live_map',    path: '/fleet/map',       icon: Map,           permission: ['can_location_map', 'can_location_own'] },
      { labelKey: 'nav.vehicles',    path: '/fleet/vehicles',  icon: Truck,         permission: ['can_vehicle_all', 'can_vehicle_own'] },
      { labelKey: 'nav.routes',      path: '/fleet/routes',    icon: Route,         permission: ['can_route_all', 'can_route_own'] },
      { labelKey: 'nav.geofences',   path: '/fleet/geofences', icon: MapPin,        permission: ['can_geofence_all', 'can_geofence_own'] },
      { labelKey: 'nav.maintenance', path: '/maintenance',     icon: Wrench,        permission: ['can_maintenance_all', 'can_maintenance_own'] },
      // Work Orders sits next to Maintenance — same permission family
      // but separate page since the data model and workflow are
      // distinct (shop-invoice records vs scheduled tasks).
      { labelKey: 'nav.work_orders', path: '/work-orders',     icon: Receipt,       permission: ['can_maintenance_all', 'can_maintenance_own'] },
      { labelKey: 'nav.parking',     path: '/fleet/parking',   icon: ParkingSquare, permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    titleKey: 'nav.safety_group',
    items: [
      { labelKey: 'nav.driver_scorecards', path: '/safety/scorecards', icon: Trophy,        permission: ['can_scorecard_all', 'can_scorecard_own'] },
      { labelKey: 'nav.safety_events',     path: '/safety/events',     icon: AlertTriangle, permission: ['can_events_all', 'can_events_own'] },
      { labelKey: 'nav.cameras',           path: '/safety/cameras',    icon: Camera,        permission: ['can_faults'] },
      { labelKey: 'nav.alerts',            path: '/safety/alerts',     icon: Bell,          permission: ['can_alerts_all', 'can_alerts_own'] },
    ],
  },
  {
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports',       path: '/reports',               icon: FileText,   permission: null },
      { labelKey: 'nav.risk_summary',  path: '/reports/risk-summary',  icon: FileText,   permission: ['can_risk_report_all', 'can_risk_report_own'] },
      { labelKey: 'nav.subscriptions', path: '/reports/subscriptions', icon: Mail,       permission: null },
      { labelKey: 'nav.fuel_costs',    path: '/costs/fuel',            icon: Fuel,       permission: ['can_fuel_cost'] },
      { labelKey: 'nav.cost_per_mile', path: '/costs/cpm',             icon: DollarSign, permission: ['can_cost_per_mile'] },
      // Cost Reports — sits under Reports rather than next to Work
      // Orders so a manager doing the quarterly review finds it
      // alongside the other money reports.
      { labelKey: 'nav.cost_reports',  path: '/cost-reports',          icon: TrendingUp, permission: ['can_maintenance_all'] },
    ],
  },
  {
    titleKey: 'nav.workforce_group',
    items: [
      { labelKey: 'nav.drivers',         path: '/workforce/drivers', icon: IdCard,        permission: ['can_manage_driver_docs'] },
      { labelKey: 'nav.coaching',        path: '/coaching',         icon: GraduationCap, permission: ['can_coaching_admin'] },
      { labelKey: 'nav.payroll',         path: '/payroll',          icon: DollarSign,    permission: ['can_payroll_admin'] },
      { labelKey: 'nav.working_hours',   path: '/admin/work-hours', icon: Clock,         permission: ['can_manage_account'] },
      { labelKey: 'nav.team_management', path: '/admin/users',      icon: Users,         permission: ['can_manage_users'] },
      { labelKey: 'nav.invites',         path: '/admin/invites',    icon: Link,          permission: ['can_invite'] },
    ],
  },
  {
    titleKey: 'nav.admin_group',
    items: [
      { labelKey: 'nav.companies',        path: '/admin/companies',       icon: Building2,     permission: ['can_manage_companies'] },
      { labelKey: 'nav.role_permissions', path: '/admin/permissions',     icon: Shield,        permission: ['can_manage_account'] },
      { labelKey: 'nav.scorecard_rules',  path: '/admin/scorecard-rules', icon: Trophy,        permission: ['can_manage_account'] },
      { labelKey: 'nav.billing',          path: '/admin/billing',         icon: CreditCard,    permission: ['can_manage_billing'] },
      // Attachment Storage — account-level setting (where the fleet's
      // bytes land: platform disk vs the user's connected Google Drive).
      // Lives in the sidebar, not the avatar menu, because the choice
      // affects the whole account, not just the logged-in user.
      { labelKey: 'nav.storage',          path: '/admin/storage',         icon: Cloud,         permission: ['can_manage_account'] },
      { labelKey: 'nav.audit_log',        path: '/admin/audit',           icon: ClipboardList, permission: ['can_manage_users'] },
      // Account-wide settings (timezone, Telegram bot, working hours,
      // feature flags). Lives in the sidebar so admins can find it
      // without going through the user menu — personal preferences
      // (display name, language, own timezone override, quiet hours)
      // moved to /profile and stay accessible via the AvatarMenu.
      { labelKey: 'nav.settings',         path: '/admin/settings',        icon: SettingsIcon,  permission: ['can_manage_account'] },
    ],
  },
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.knowledge_base', path: '/knowledge', icon: BookOpen, permission: null },
    ],
  },
];

export default function Sidebar() {
  const { user } = useAuth();
  const { t } = useTranslation();
  // ``viewHasAny`` is the active-persona-aware permission check from
  // RoleViewContext.  For non-switchable roles (Fleet/Safety/Dispatcher/
  // Driver) it falls back to the user's own permissions.  For Owner/
  // Admin previewing as another role, it uses that role's perm set so
  // the sidebar reflects what the previewed persona would see — no
  // bouncing back to "but they're Owner so show everything".
  const { viewHasAny } = useRoleView();

  const isFeatureVisible = (item: NavItem) => {
    if (item.path === '/payroll') return user?.payroll_enabled !== false;
    if (item.path === '/coaching') return user?.coaching_enabled !== false;
    return true;
  };

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (!isFeatureVisible(item)) return false;
      if (!item.permission) return true;
      const flags = Array.isArray(item.permission) ? item.permission : [item.permission];
      return viewHasAny(...flags);
    });

  return (
    <aside className="w-56 bg-card border-r border-border flex flex-col shrink-0 h-screen">
      {/* Logo + persona selector — sit on the same row so the persona
          control reads as part of "where am I" rather than "who am I".
          The selector lives next to the 4truck mark, right-aligned so
          there's a clear visual gap between brand and view-control. */}
      <div className="h-14 flex items-center px-3 border-b border-border shrink-0 gap-2">
        <Truck size={20} className="text-primary shrink-0" />
        <span className="text-lg font-bold text-foreground">4truck</span>
        <div className="ml-auto">
          <PersonaSelector />
        </div>
      </div>

      {/* Nav — scrolls independently */}
      <nav className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {NAV_GROUPS.map((group, gi) => {
          const items = filterItems(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.titleKey ?? `_top-${gi}`}>
              {group.titleKey && (
                <div className="px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  {t(group.titleKey)}
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
                    {t(item.labelKey)}
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
