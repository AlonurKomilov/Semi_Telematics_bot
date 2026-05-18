import { Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense, type ReactNode } from 'react';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';

/**
 * Wrap ``lazy(() => import(...))`` so a "chunk-not-found" failure
 * (deploy rotated the Vite content hashes while the user's
 * index.html was still cached client-side) triggers a one-shot hard
 * reload instead of leaving the SPA wedged on a white screen with
 * ``Failed to fetch dynamically imported module``.
 *
 * We guard against reload loops with a sessionStorage flag — if a
 * reload already happened for this session and we still can't load
 * the chunk, surface the error so it shows up in the error boundary
 * rather than spinning forever.
 */
function lazyWithReload<T extends { default: React.ComponentType<unknown> }>(
  loader: () => Promise<T>,
): React.LazyExoticComponent<React.ComponentType<unknown>> {
  return lazy(() =>
    loader().catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      const isChunkLoadError =
        msg.includes('Failed to fetch dynamically imported module') ||
        msg.includes('Importing a module script failed') ||
        msg.includes('error loading dynamically imported module');
      const alreadyReloaded = sessionStorage.getItem('chunk-reload-attempted');
      if (isChunkLoadError && !alreadyReloaded) {
        sessionStorage.setItem('chunk-reload-attempted', String(Date.now()));
        window.location.reload();
        // Reload is async — return a never-resolving promise so React
        // doesn't try to render the broken module in the meantime.
        return new Promise<T>(() => {});
      }
      // Either it's a non-chunk error (real bug) or we already tried
      // the reload — let the error bubble so the boundary can show it.
      throw err;
    }),
  );
}

// ── Lazy pages ──────────────────────────────────────────────────────
// Each page becomes its own JS chunk so the initial dashboard payload
// only contains the shell + the landing route.  Suspense fallback
// is the lightweight ``<RouteSkeleton/>`` (no extra deps).
//
// Using lazyWithReload so a stale-hash chunk request (post-deploy
// browser cache miss) auto-recovers by reloading instead of dying
// on ``Failed to fetch dynamically imported module``.
const Overview         = lazyWithReload(() => import('./pages/Overview'));
const Vehicles         = lazyWithReload(() => import('./pages/fleet/Vehicles'));
const VehicleDetail    = lazyWithReload(() => import('./pages/fleet/VehicleDetail'));
const LiveMap          = lazyWithReload(() => import('./pages/fleet/LiveMap'));
const Alerts           = lazyWithReload(() => import('./pages/dispatch/Alerts'));
const Geofences        = lazyWithReload(() => import('./pages/dispatch/Geofences'));
const RoutesPage       = lazyWithReload(() => import('./pages/dispatch/Routes'));
const Scorecards       = lazyWithReload(() => import('./pages/safety/Scorecards'));
const Events           = lazyWithReload(() => import('./pages/safety/Events'));
const Cameras          = lazyWithReload(() => import('./pages/safety/Cameras'));
const Parking          = lazyWithReload(() => import('./pages/safety/Parking'));
const Reports          = lazyWithReload(() => import('./pages/reports/Reports'));
const Subscriptions    = lazyWithReload(() => import('./pages/reports/Subscriptions'));
const RiskSummary      = lazyWithReload(() => import('./pages/reports/RiskSummary'));
const FuelCosts        = lazyWithReload(() => import('./pages/costs/FuelCosts'));
const CostPerMile      = lazyWithReload(() => import('./pages/costs/CostPerMile'));
const Maintenance      = lazyWithReload(() => import('./pages/maintenance/Tasks'));
const WorkOrders       = lazyWithReload(() => import('./pages/work-orders/WorkOrders'));
const WorkOrderForm    = lazyWithReload(() => import('./pages/work-orders/WorkOrderForm'));
const CostReports      = lazyWithReload(() => import('./pages/work-orders/Reports'));
const KnowledgeBase    = lazyWithReload(() => import('./pages/knowledge/KnowledgeBase'));
const Users            = lazyWithReload(() => import('./pages/admin/Users'));
const Companies        = lazyWithReload(() => import('./pages/admin/Companies'));
const AuditLog         = lazyWithReload(() => import('./pages/admin/AuditLog'));
const Settings         = lazyWithReload(() => import('./pages/admin/Settings'));
const Profile          = lazyWithReload(() => import('./pages/Profile'));
const Storage          = lazyWithReload(() => import('./pages/admin/Storage'));
const WorkHours        = lazyWithReload(() => import('./pages/admin/WorkHours'));
const Invites          = lazyWithReload(() => import('./pages/admin/Invites'));
const RolePermissions  = lazyWithReload(() => import('./pages/admin/RolePermissions'));
const ScorecardRules   = lazyWithReload(() => import('./pages/admin/ScorecardRules'));
const Billing          = lazyWithReload(() => import('./pages/admin/Billing'));
const Payroll          = lazyWithReload(() => import('./pages/payroll/Payroll'));
const Coaching         = lazyWithReload(() => import('./pages/coaching/Coaching'));
const Drivers          = lazyWithReload(() => import('./pages/workforce/Drivers'));
const AIChat           = lazyWithReload(() => import('./pages/ai/Chat'));
const AISummary        = lazyWithReload(() => import('./pages/ai/Summary'));
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

        {/* Fleet (cont.) — routes & geofences live with fleet operations */}
        <Route path="fleet/routes" element={L(<P perm={['can_route_all', 'can_route_own']}><RoutesPage /></P>)} />
        <Route path="fleet/geofences" element={L(<P perm={['can_geofence_all', 'can_geofence_own']}><Geofences /></P>)} />
        <Route path="fleet/parking" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Parking /></P>)} />

        {/* Safety */}
        <Route path="safety/scorecards" element={L(<P perm={['can_scorecard_all', 'can_scorecard_own']}><Scorecards /></P>)} />
        <Route path="safety/events" element={L(<P perm={['can_events_all', 'can_events_own']}><Events /></P>)} />
        <Route path="safety/cameras" element={L(<P perm="can_faults"><Cameras /></P>)} />
        <Route path="safety/alerts" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Alerts /></P>)} />

        {/* Legacy URL redirects (preserve bookmarks) */}
        <Route path="dispatch/alerts" element={<Navigate to="/safety/alerts" replace />} />
        <Route path="dispatch/routes" element={<Navigate to="/fleet/routes" replace />} />
        <Route path="dispatch/geofences" element={<Navigate to="/fleet/geofences" replace />} />
        <Route path="safety/parking" element={<Navigate to="/fleet/parking" replace />} />

        {/* AI */}
        <Route path="ai/chat" element={L(<P perm="can_faults"><AIChat /></P>)} />
        <Route path="ai/summary" element={L(<P perm="can_faults"><AISummary /></P>)} />

        {/* Reports */}
        <Route path="reports" element={L(<P perm="can_faults"><Reports /></P>)} />
        <Route path="reports/subscriptions" element={L(<Subscriptions />)} />
        <Route path="reports/risk-summary" element={L(<P perm={['can_risk_report_all', 'can_risk_report_own']}><RiskSummary /></P>)} />

        {/* Costs */}
        <Route path="costs/fuel" element={L(<P perm="can_fuel_cost"><FuelCosts /></P>)} />
        <Route path="costs/cpm" element={L(<P perm="can_cost_per_mile"><CostPerMile /></P>)} />

        {/* Maintenance */}
        <Route path="maintenance" element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><Maintenance /></P>)} />

        {/* Work Orders — separate module from Maintenance.  Maintenance
            tracks "what needs doing"; Work Orders is "what was done"
            (shop visits, costs, parts, attachments). */}
        <Route path="work-orders"         element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><WorkOrders /></P>)} />
        <Route path="work-orders/new"     element={L(<P perm="can_maintenance_all"><WorkOrderForm /></P>)} />
        <Route path="work-orders/:id"     element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><WorkOrderForm /></P>)} />
        {/* Cost Reports — managers only, since aggregate spend data
            is sensitive and the click-through reveals other trucks'
            details. */}
        <Route path="cost-reports"        element={L(<P perm="can_maintenance_all"><CostReports /></P>)} />

        {/* Knowledge Base */}
        <Route path="knowledge" element={L(<KnowledgeBase />)} />

        {/* Admin */}
        <Route path="admin/users" element={L(<P perm="can_manage_users"><Users /></P>)} />
        <Route path="admin/companies" element={L(<P perm="can_manage_companies"><Companies /></P>)} />
        <Route path="admin/audit" element={L(<P perm="can_manage_users"><AuditLog /></P>)} />
        <Route path="admin/work-hours" element={L(<P perm="can_manage_account"><WorkHours /></P>)} />
        <Route path="admin/invites" element={L(<P perm="can_invite"><Invites /></P>)} />
        <Route path="admin/settings" element={L(<P perm="can_manage_account"><Settings /></P>)} />
        {/* Personal preferences — accessible to every authenticated
            user regardless of role. */}
        <Route path="profile" element={L(<Profile />)} />
        <Route path="admin/storage"  element={L(<P perm="can_manage_account"><Storage /></P>)} />
        <Route path="admin/permissions" element={L(<P perm="can_manage_account"><RolePermissions /></P>)} />
        <Route path="admin/scorecard-rules" element={L(<P perm="can_manage_account"><ScorecardRules /></P>)} />
        <Route path="admin/billing" element={L(<P perm="can_manage_billing"><Billing /></P>)} />
        <Route path="payroll" element={L(<P perm="can_payroll_admin"><Payroll /></P>)} />
        <Route path="coaching" element={L(<P perm="can_coaching_admin"><Coaching /></P>)} />
        <Route path="workforce/drivers" element={L(<P perm="can_manage_driver_docs"><Drivers /></P>)} />
        <Route path="*" element={L(<NotFound />)} />
      </Route>
    </Routes>
  );
}
