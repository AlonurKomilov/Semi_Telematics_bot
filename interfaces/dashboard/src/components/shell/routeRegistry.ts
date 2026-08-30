import {
  LayoutDashboard, Truck, Bell, Bot, FileText, Mail, MapPin, BookOpen,
  Wrench, Map, Route, Trophy, AlertTriangle, TrendingUp,
  Camera, ParkingSquare, Fuel, DollarSign, Users, Shield, Building2,
  Link as LinkIcon, ClipboardList, CreditCard, GraduationCap,
  IdCard, Gauge, Boxes, Receipt, Store, Cog, ClipboardCheck, Package,
  UserPlus, Plug, Cloud,
  BadgeDollarSign,
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
  { label: 'AI Assistant', path: '/ai/chat', icon: Bot,             group: 'Home',   permission: 'can_ai_chat',
    description: 'Ask AI about vehicles, faults, trips, and events', keywords: ['chat','assistant','gpt'] },

  // Fleet
  { label: 'Live Map',    path: '/live-map',       icon: Map,           group: 'Fleet',  permission: ['can_location_map', 'can_location_vehicle'],
    description: 'Real-time map of every vehicle', keywords: ['map','gps','location'] },
  { label: 'Vehicles',    path: '/vehicles',  icon: Truck,         group: 'Fleet',  permission: ['can_vehicle_all', 'can_vehicle_vehicle'],
    description: 'List of trucks, status, fuel and faults', keywords: ['trucks','assets'] },
  { label: 'Routes',      path: '/routes',    icon: Route,         group: 'Fleet',  permission: ['can_route_all', 'can_route_vehicle'],
    description: 'Trip history and active routes', keywords: ['trips','dispatch'] },
  { label: 'Geofences',   path: '/geofences', icon: MapPin,        group: 'Fleet',  permission: ['can_geofence_all', 'can_geofence_vehicle'],
    description: 'Zones that trigger arrival/exit alerts', keywords: ['zones','boundaries'] },
  { label: 'Maintenance', path: '/maintenance',     icon: Wrench,        group: 'Fleet',  permission: ['can_maintenance_all', 'can_maintenance_vehicle'],
    description: 'Scheduled service and open tasks', keywords: ['service','repair','tasks'] },
  { label: 'Parking',     path: '/parking',   icon: ParkingSquare, group: 'Fleet',  permission: ['can_parking_all', 'can_parking_vehicle'],
    description: 'Where drivers park and safety classification', keywords: ['parking','safe'] },

  // Safety
  { label: 'Scorecards', path: '/scorecards', icon: Trophy,        group: 'Safety', permission: ['can_scorecard_all', 'can_scorecard_vehicle'],
    description: 'Driver behaviour scoring and ranking', keywords: ['drivers','score','behavior'] },
  { label: 'Safety Events',     path: '/safety-events',     icon: AlertTriangle, group: 'Safety', permission: ['can_events_all', 'can_events_vehicle'],
    description: 'Harsh braking, speeding, and crash events', keywords: ['harsh','speeding','crash'] },
  { label: 'Cameras',           path: '/cameras',    icon: Camera,        group: 'Safety', permission: ['can_cameras'],
    description: 'Vehicle dashcam footage', keywords: ['video','dashcam'] },
  { label: 'Alerts',            path: '/alerts',     icon: Bell,          group: 'Safety', permission: ['can_alerts_all', 'can_alerts_vehicle'],
    description: 'Pending notifications across all vehicles', keywords: ['notifications','warnings'] },

  // Reports
  { label: 'Reports',       path: '/reports',               icon: FileText,   group: 'Reports', permission: null,
    description: 'Generate & download operational reports', keywords: ['csv','pdf','export'] },
  { label: 'Risk Summary',  path: '/reports/risk-summary',  icon: FileText,   group: 'Reports', permission: ['can_risk_report_all', 'can_risk_report_own'],
    description: 'Risk profile across drivers and vehicles', keywords: ['risk','insurance'] },
  { label: 'Cost Reports',  path: '/reports/cost-reports',  icon: TrendingUp, group: 'Reports', permission: ['can_cost_reports'],
    description: 'Maintenance cost rollups: per-vehicle, per-task, per-vendor', keywords: ['cost','spend','maintenance','rollup'] },
  { label: 'Scheduled Reports', path: '/reports/scheduled-reports', icon: Mail, group: 'Reports', permission: null,
    description: 'Recurring report deliveries via Telegram', keywords: ['schedule','recurring','telegram','pdf','subscription'] },
  { label: 'Fuel Costs',    path: '/costs/fuel',            icon: Fuel,       group: 'Reports', permission: ['can_fuel_cost'],
    description: 'Fuel spend per vehicle and per mile', keywords: ['fuel','spend','cost'] },
  { label: 'Cost per Mile', path: '/costs/cpm',             icon: DollarSign, group: 'Reports', permission: ['can_cost_per_mile'],
    description: 'Operating cost per mile by truck', keywords: ['cpm','cost','per mile'] },

  // Workforce
  { label: 'Drivers',         path: '/workforce/drivers', icon: IdCard,        group: 'Workforce', permission: ['can_manage_driver_docs'],
    description: 'Driver profiles, CDL/medical docs, and vehicle assignments', keywords: ['cdl','medical','driver','documents'] },
  { label: 'Coaching',        path: '/coaching',         icon: GraduationCap, group: 'Workforce', permission: ['can_coaching_admin'],
    description: 'Driver coaching assignments and acks', keywords: ['training','review'] },
  { label: 'Driver Pay',      path: '/driver-pay',          icon: DollarSign,    group: 'Workforce', permission: ['can_driver_pay_admin'],
    description: 'Driver paystubs and pay rules', keywords: ['pay','salary','wages'] },
  // Working Hours is NOT listed: it was folded into Team Management ->
  // Working Hours, and its sidebar entry was removed on purpose so
  // operators don't see two doors to the same config (featureCatalog says
  // so).  ``/work-hours`` still resolves as a Navigate redirect for old
  // bookmarks — but search advertising it would put the second door
  // straight back.  Its keywords move onto Team Management instead, so
  // typing "hos" or "shift" still lands somewhere useful.
  { label: 'Team Management', path: '/team',      icon: Users,         group: 'Workforce', permission: ['can_manage_users'],
    description: 'Add or remove users, set roles', keywords: ['users','staff','team','hos','hours','shift','working hours'] },
  { label: 'Invites',         path: '/invites',    icon: LinkIcon,      group: 'Workforce', permission: ['can_invite'],
    description: 'Generate invite links for new staff', keywords: ['invite','onboard'] },

  // Admin
  { label: 'Companies',        path: '/companies',       icon: Building2,     group: 'Admin', permission: ['can_manage_companies'],
    description: 'Sub-companies in your account', keywords: ['org','tenants'] },
  { label: 'Permissions',      path: '/permissions',     icon: Shield,        group: 'Admin', permission: ['can_manage_permissions'],
    description: 'What each role is allowed to do', keywords: ['rbac','roles','permissions'] },
  { label: 'Scorecard Rules',  path: '/scorecard-rules', icon: Trophy,        group: 'Safety', permission: ['can_manage_config_all'],
    description: 'Rules that build the scorecard', keywords: ['scoring','penalties','config'] },
  { label: 'Billing & Plan',   path: '/billing',         icon: CreditCard,    group: 'Admin', permission: ['can_manage_billing'],
    description: 'Billing, plan and invoices', keywords: ['plan','subscription','invoice','billing'] },
  { label: 'Audit Log',        path: '/audit',           icon: ClipboardList, group: 'Admin', permission: ['can_manage_users'],
    description: 'Who changed what, when', keywords: ['log','history','audit'] },
  { label: 'Settings',         path: '/settings',        icon: Shield,        group: 'Admin', permission: ['can_manage_account'],
    description: 'Account-wide settings and integrations', keywords: ['account','config','settings'] },

  // Knowledge
  { label: 'Knowledge Base', path: '/knowledge', icon: BookOpen, group: 'Help', permission: null,
    description: 'Internal docs, SOPs, and guides', keywords: ['docs','help','wiki'] },
  // ── Added after a responsive audit found the palette could not reach
  //    13 live routes.  This list is a hand-kept SIBLING of
  //    config/featureCatalog.ts — it exists because search needs three
  //    things the catalog does not carry: an English label to match on
  //    (the catalog holds an i18n KEY), a one-line description, and
  //    keywords for the words people actually type.  A drift guard in
  //    routeRegistry.test.ts now fails the build when the catalog gains
  //    a route this list has not.
  { label: 'Loads', path: '/loads', icon: Package, group: 'Fleet',
    permission: ['can_loads_all', 'can_loads_own'],
    description: 'Freight — entered by hand or synced from your TMS',
    keywords: ['load', 'freight', 'trip', 'dispatch', 'rate'] },
  { label: 'Work Orders', path: '/work-orders', icon: Receipt, group: 'Fleet',
    permission: ['can_work_orders_all', 'can_work_orders_vehicle'],
    description: 'Shop visits, labour, parts and invoices',
    keywords: ['wo', 'repair', 'shop', 'invoice', 'labour', 'labor'] },
  { label: 'Inspections', path: '/inspections', icon: ClipboardCheck, group: 'Fleet',
    permission: ['can_inspections_all', 'can_inspections_vehicle'],
    description: 'DVIR and scheduled inspection records',
    keywords: ['dvir', 'inspect', 'defect', 'pti'] },
  { label: 'Parts', path: '/parts', icon: Cog, group: 'Fleet',
    permission: 'can_parts',
    description: 'Parts catalogue and stock',
    keywords: ['part', 'inventory', 'stock', 'sku'] },
  { label: 'Vendors', path: '/vendors', icon: Store, group: 'Fleet',
    permission: 'can_work_orders_all',
    description: 'Repair shops and suppliers',
    keywords: ['vendor', 'shop', 'supplier', 'garage'] },
  { label: 'Service Tasks', path: '/service-tasks', icon: ClipboardList, group: 'Fleet',
    permission: 'can_service_tasks',
    description: 'The shared job vocabulary behind maintenance and work orders',
    keywords: ['task', 'service', 'job', 'pm'] },
  { label: 'Vehicle Inventory', path: '/vehicles/inventory', icon: Boxes, group: 'Fleet',
    permission: ['can_vehicle_all', 'can_vehicle_vehicle'],
    description: 'Onboard items per truck — cameras, fuel cards, ELDs',
    keywords: ['inventory', 'item', 'camera', 'eld', 'fuel card', 'toll'] },
  { label: 'Vehicle Documents', path: '/vehicles/documents', icon: FileText, group: 'Fleet',
    permission: ['can_vehicle_docs'],
    description: "Registration, title, insurance and annual inspections — every truck's papers",
    keywords: ['document', 'registration', 'title', 'insurance', 'inspection',
               'expiry', 'expires', 'paperwork', 'cab card'] },
  { label: 'KPI', path: '/kpi', icon: Gauge, group: 'Reports',
    permission: ['can_kpi'],
    description: 'Account-wide performance analytics',
    keywords: ['kpi', 'metric', 'performance', 'analytics'] },
  // Self-scoped page — no can_* flag; the endpoint returns only the
  // caller's own finalized payout rows.
  { label: 'My payouts', path: '/kpi/my-payouts', icon: BadgeDollarSign, group: 'Reports',
    permission: null,
    keywords: ['payout', 'incentive', 'my pay', 'bonus'] },
  { label: 'Driver Applications', path: '/workforce/applications', icon: UserPlus, group: 'Workforce',
    permission: ['can_manage_applications'],
    description: 'Recruiting intake — apply links and applicants',
    keywords: ['applicant', 'recruit', 'hire', 'apply', 'dqf'] },
  { label: 'Carrier Directory', path: '/workforce/carrier-directory', icon: Building2, group: 'Workforce',
    permission: ['can_carrier_directory'],
    description: 'Reference info for the carriers we recruit for',
    keywords: ['carrier', 'directory', 'prequal'] },
  { label: 'Integrations', path: '/integrations', icon: Plug, group: 'Admin',
    permission: ['can_manage_integrations'],
    description: 'Connect Samsara, Datatruck and other systems',
    keywords: ['integration', 'samsara', 'datatruck', 'motive', 'sync', 'api'] },
  { label: 'Storage', path: '/object-storage', icon: Cloud, group: 'Admin',
    permission: ['can_manage_storage'],
    description: 'Where uploaded files live, and how much space they use',
    keywords: ['storage', 'file', 'upload', 'drive', 'space'] },
];

export function findRouteByPath(pathname: string): RouteEntry | undefined {
  // exact match first; otherwise prefix match (longest wins)
  const exact = ROUTE_ENTRIES.find((r) => r.path === pathname);
  if (exact) return exact;
  const sorted = [...ROUTE_ENTRIES].sort((a, b) => b.path.length - a.path.length);
  return sorted.find((r) => r.path !== '/' && pathname.startsWith(r.path));
}
