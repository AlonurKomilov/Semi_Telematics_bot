import { Routes, Route } from 'react-router-dom';
import { lazy, Suspense, type ReactNode } from 'react';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';

// ── Lazy pages ──────────────────────────────────────────────────────
// Each page becomes its own JS chunk so the initial dashboard payload
// only contains the shell + the landing route.  Suspense fallback
// is the lightweight ``<RouteSkeleton/>`` (no extra deps).
const Overview         = lazy(() => import('./pages/Overview'));
const Vehicles         = lazy(() => import('./pages/fleet/Vehicles'));
const VehicleDetail    = lazy(() => import('./pages/fleet/VehicleDetail'));
const LiveMap          = lazy(() => import('./pages/fleet/LiveMap'));
const Weather          = lazy(() => import('./pages/fleet/Weather'));
const Alerts           = lazy(() => import('./pages/dispatch/Alerts'));
const Geofences        = lazy(() => import('./pages/dispatch/Geofences'));
const RoutesPage       = lazy(() => import('./pages/dispatch/Routes'));
const Scorecards       = lazy(() => import('./pages/safety/Scorecards'));
const Events           = lazy(() => import('./pages/safety/Events'));
const Cameras          = lazy(() => import('./pages/safety/Cameras'));
const Parking          = lazy(() => import('./pages/safety/Parking'));
const Reports          = lazy(() => import('./pages/reports/Reports'));
const Subscriptions    = lazy(() => import('./pages/reports/Subscriptions'));
const FuelCosts        = lazy(() => import('./pages/costs/FuelCosts'));
const CostPerMile      = lazy(() => import('./pages/costs/CostPerMile'));
const Maintenance      = lazy(() => import('./pages/maintenance/Tasks'));
const KnowledgeBase    = lazy(() => import('./pages/knowledge/KnowledgeBase'));
const Users            = lazy(() => import('./pages/admin/Users'));
const Companies        = lazy(() => import('./pages/admin/Companies'));
const AuditLog         = lazy(() => import('./pages/admin/AuditLog'));
const Settings         = lazy(() => import('./pages/admin/Settings'));
const WorkHours        = lazy(() => import('./pages/admin/WorkHours'));
const Invites          = lazy(() => import('./pages/admin/Invites'));
const RolePermissions  = lazy(() => import('./pages/admin/RolePermissions'));
const ScorecardRules   = lazy(() => import('./pages/admin/ScorecardRules'));
const Billing          = lazy(() => import('./pages/admin/Billing'));
const AIChat           = lazy(() => import('./pages/ai/Chat'));
const AISummary        = lazy(() => import('./pages/ai/Summary'));
const NotFound         = lazy(() => import('./pages/NotFound'));

function RouteSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-2 animate-pulse" aria-label="Loading page">
      <div className="h-8 w-1/3 bg-muted rounded" />
      <div className="h-4 w-1/2 bg-muted rounded" />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
        <div className="h-24 bg-muted rounded" />
        <div className="h-24 bg-muted rounded" />
        <div className="h-24 bg-muted rounded" />
      </div>
      <div className="h-72 bg-muted rounded mt-2" />
    </div>
  );
}

function P({ perm, children }: { perm: string | string[]; children: ReactNode }) {
  return <ProtectedRoute permission={perm}>{children}</ProtectedRoute>;
}

// Wrap each lazy element in Suspense so the skeleton renders inside the
// Layout's main column rather than swapping the whole shell.
function L(node: ReactNode) {
  return <Suspense fallback={<RouteSkeleton />}>{node}</Suspense>;
}

export default function AppRouter() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={L(<Overview />)} />

        {/* Fleet */}
        <Route path="fleet/vehicles" element={L(<P perm={['can_vehicle_all', 'can_vehicle_own']}><Vehicles /></P>)} />
        <Route path="fleet/vehicle/:name" element={L(<P perm={['can_vehicle_all', 'can_vehicle_own']}><VehicleDetail /></P>)} />
        <Route path="fleet/map" element={L(<P perm={['can_location_map', 'can_location_own']}><LiveMap /></P>)} />
        <Route path="fleet/weather" element={L(<P perm="can_faults"><Weather /></P>)} />

        {/* Dispatch */}
        <Route path="dispatch/alerts" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Alerts /></P>)} />
        <Route path="dispatch/geofences" element={L(<P perm={['can_geofence_all', 'can_geofence_own']}><Geofences /></P>)} />
        <Route path="dispatch/routes" element={L(<P perm={['can_route_all', 'can_route_own']}><RoutesPage /></P>)} />

        {/* Safety */}
        <Route path="safety/scorecards" element={L(<P perm={['can_scorecard_all', 'can_scorecard_own']}><Scorecards /></P>)} />
        <Route path="safety/events" element={L(<P perm={['can_events_all', 'can_events_own']}><Events /></P>)} />
        <Route path="safety/cameras" element={L(<P perm="can_faults"><Cameras /></P>)} />
        <Route path="safety/parking" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Parking /></P>)} />

        {/* AI */}
        <Route path="ai/chat" element={L(<P perm="can_faults"><AIChat /></P>)} />
        <Route path="ai/summary" element={L(<P perm="can_faults"><AISummary /></P>)} />

        {/* Reports */}
        <Route path="reports" element={L(<P perm="can_faults"><Reports /></P>)} />
        <Route path="reports/subscriptions" element={L(<Subscriptions />)} />

        {/* Costs */}
        <Route path="costs/fuel" element={L(<P perm="can_fuel_cost"><FuelCosts /></P>)} />
        <Route path="costs/cpm" element={L(<P perm="can_cost_per_mile"><CostPerMile /></P>)} />

        {/* Maintenance */}
        <Route path="maintenance" element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><Maintenance /></P>)} />

        {/* Knowledge Base */}
        <Route path="knowledge" element={L(<KnowledgeBase />)} />

        {/* Admin */}
        <Route path="admin/users" element={L(<P perm="can_manage_users"><Users /></P>)} />
        <Route path="admin/companies" element={L(<P perm="can_manage_companies"><Companies /></P>)} />
        <Route path="admin/audit" element={L(<P perm="can_manage_users"><AuditLog /></P>)} />
        <Route path="admin/work-hours" element={L(<P perm="can_manage_account"><WorkHours /></P>)} />
        <Route path="admin/invites" element={L(<P perm="can_invite"><Invites /></P>)} />
        <Route path="admin/settings" element={L(<P perm="can_manage_account"><Settings /></P>)} />
        <Route path="admin/permissions" element={L(<P perm="can_manage_account"><RolePermissions /></P>)} />
        <Route path="admin/scorecard-rules" element={L(<P perm="can_manage_account"><ScorecardRules /></P>)} />
        <Route path="admin/billing" element={L(<P perm="can_manage_billing"><Billing /></P>)} />
        <Route path="*" element={L(<NotFound />)} />
      </Route>
    </Routes>
  );
}
