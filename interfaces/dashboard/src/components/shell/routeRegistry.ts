import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Wrench, Map, Route, Trophy, AlertTriangle,
  Camera, ParkingSquare, Fuel, DollarSign, Users, Shield, Building2,
  Link as LinkIcon, ClipboardList, Clock, CreditCard, GraduationCap,
  IdCard,
  type LucideIcon,
} from 'lucide-react';

export interface RouteEntry {
  label: string;
  path: string;
  icon: LucideIcon;
  group: string;
  permission: string | string[] | null;
  description?: string;
  keywords?: string[];
}

export const ROUTE_ENTRIES: RouteEntry[] = [
  // Top
  { label: 'Overview',     path: '/',        icon: LayoutDashboard, group: 'Home',   permission: null,
    description: 'Status at a glance — adapts to your role', keywords: ['home','dashboard','start'] },
  { label: 'AI Assistant', path: '/ai/chat', icon: Bot,             group: 'Home',   permission: 'can_faults',
    description: 'Ask AI about vehicles, faults, trips, and events', keywords: ['chat','assistant','gpt'] },

  // Fleet
  { label: 'Live Map',    path: '/live-map',       icon: Map,           group: 'Fleet',  permission: ['can_location_map', 'can_location_own'],
    description: 'Real-time map of every vehicle', keywords: ['map','gps','location'] },
  { label: 'Vehicles',    path: '/vehicles',  icon: Truck,         group: 'Fleet',  permission: ['can_vehicle_all', 'can_vehicle_own'],
    description: 'List of trucks, status, fuel and faults', keywords: ['trucks','assets'] },
  { label: 'Routes',      path: '/routes',    icon: Route,         group: 'Fleet',  permission: ['can_route_all', 'can_route_own'],
    description: 'Trip history and active routes', keywords: ['trips','dispatch'] },
  { label: 'Geofences',   path: '/geofences', icon: MapPin,        group: 'Fleet',  permission: ['can_geofence_all', 'can_geofence_own'],
    description: 'Zones that trigger arrival/exit alerts', keywords: ['zones','boundaries'] },
  { label: 'Maintenance', path: '/maintenance',     icon: Wrench,        group: 'Fleet',  permission: ['can_maintenance_all', 'can_maintenance_own'],
    description: 'Scheduled service and open tasks', keywords: ['service','repair','tasks'] },
  { label: 'Parking',     path: '/parking',   icon: ParkingSquare, group: 'Fleet',  permission: ['can_alerts_all', 'can_alerts_own'],
    description: 'Where drivers park and safety classification', keywords: ['parking','safe'] },

  // Safety
  { label: 'Driver Scorecards', path: '/driver-scorecards', icon: Trophy,        group: 'Safety', permission: ['can_scorecard_all', 'can_scorecard_own'],
    description: 'Driver behaviour scoring and ranking', keywords: ['drivers','score','behavior'] },
  { label: 'Safety Events',     path: '/safety-events',     icon: AlertTriangle, group: 'Safety', permission: ['can_events_all', 'can_events_own'],
    description: 'Harsh braking, speeding, and crash events', keywords: ['harsh','speeding','crash'] },
  { label: 'Cameras',           path: '/cameras',    icon: Camera,        group: 'Safety', permission: ['can_faults'],
    description: 'Vehicle dashcam footage', keywords: ['video','dashcam'] },
  { label: 'Alerts',            path: '/alerts',     icon: Bell,          group: 'Safety', permission: ['can_alerts_all', 'can_alerts_own'],
    description: 'Pending notifications across all vehicles', keywords: ['notifications','warnings'] },

  // Reports
  { label: 'Reports',       path: '/reports',               icon: FileText,   group: 'Reports', permission: null,
    description: 'Generate & download operational reports', keywords: ['csv','pdf','export'] },
  { label: 'Risk Summary',  path: '/reports/risk-summary',  icon: FileText,   group: 'Reports', permission: ['can_risk_report_all', 'can_risk_report_own'],
    description: 'Risk profile across drivers and vehicles', keywords: ['risk','insurance'] },
  { label: 'Subscriptions', path: '/reports/subscriptions', icon: Mail,       group: 'Reports', permission: null,
    description: 'Scheduled email reports', keywords: ['email','schedule'] },
  { label: 'Fuel Costs',    path: '/costs/fuel',            icon: Fuel,       group: 'Reports', permission: ['can_fuel_cost'],
    description: 'Fuel spend per vehicle and per mile', keywords: ['fuel','spend','cost'] },
  { label: 'Cost per Mile', path: '/costs/cpm',             icon: DollarSign, group: 'Reports', permission: ['can_cost_per_mile'],
    description: 'Operating cost per mile by truck', keywords: ['cpm','cost','per mile'] },

  // Workforce
  { label: 'Drivers',         path: '/workforce/drivers', icon: IdCard,        group: 'Workforce', permission: ['can_manage_driver_docs'],
    description: 'Driver profiles, CDL/medical docs, and vehicle assignments', keywords: ['cdl','medical','driver','documents'] },
  { label: 'Coaching',        path: '/coaching',         icon: GraduationCap, group: 'Workforce', permission: ['can_coaching_admin'],
    description: 'Driver coaching assignments and acks', keywords: ['training','review'] },
  { label: 'Payroll',         path: '/payroll',          icon: DollarSign,    group: 'Workforce', permission: ['can_payroll_admin'],
    description: 'Driver paystubs and pay rules', keywords: ['pay','salary','wages'] },
  { label: 'Working Hours',   path: '/admin/work-hours', icon: Clock,         group: 'Workforce', permission: ['can_manage_account'],
    description: 'Driver hours, HOS and shifts', keywords: ['hos','hours','shift'] },
  { label: 'Team Management', path: '/admin/users',      icon: Users,         group: 'Workforce', permission: ['can_manage_users'],
    description: 'Add or remove users, set roles', keywords: ['users','staff','team'] },
  { label: 'Invites',         path: '/admin/invites',    icon: LinkIcon,      group: 'Workforce', permission: ['can_invite'],
    description: 'Generate invite links for new staff', keywords: ['invite','onboard'] },

  // Admin
  { label: 'Companies',        path: '/admin/companies',       icon: Building2,     group: 'Admin', permission: ['can_manage_companies'],
    description: 'Sub-companies in your account', keywords: ['org','tenants'] },
  { label: 'Role Permissions', path: '/admin/permissions',     icon: Shield,        group: 'Admin', permission: ['can_manage_account'],
    description: 'What each role is allowed to do', keywords: ['rbac','roles'] },
  { label: 'Scorecard Rules',  path: '/admin/scorecard-rules', icon: Trophy,        group: 'Admin', permission: ['can_manage_account'],
    description: 'Rules that build the driver scorecard', keywords: ['scoring','penalties'] },
  { label: 'Billing & Plan',   path: '/admin/billing',         icon: CreditCard,    group: 'Admin', permission: ['can_manage_billing'],
    description: 'Subscription plan and invoices', keywords: ['plan','subscription','invoice'] },
  { label: 'Audit Log',        path: '/admin/audit',           icon: ClipboardList, group: 'Admin', permission: ['can_manage_users'],
    description: 'Who changed what, when', keywords: ['log','history','audit'] },
  { label: 'Settings',         path: '/admin/settings',        icon: Shield,        group: 'Admin', permission: ['can_manage_account'],
    description: 'Account-wide settings and integrations', keywords: ['account','config','settings'] },

  // Knowledge
  { label: 'Knowledge Base', path: '/knowledge', icon: BookOpen, group: 'Help', permission: null,
    description: 'Internal docs, SOPs, and guides', keywords: ['docs','help','wiki'] },
];

export function findRouteByPath(pathname: string): RouteEntry | undefined {
  // exact match first; otherwise prefix match (longest wins)
  const exact = ROUTE_ENTRIES.find((r) => r.path === pathname);
  if (exact) return exact;
  const sorted = [...ROUTE_ENTRIES].sort((a, b) => b.path.length - a.path.length);
  return sorted.find((r) => r.path !== '/' && pathname.startsWith(r.path));
}
