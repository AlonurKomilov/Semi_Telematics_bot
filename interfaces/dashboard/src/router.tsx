import { Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense, type ReactNode } from 'react';
// Shell selection: instead of a single hardcoded Layout, the router
// resolves which shell to render based on the active persona via
// :func:`pickShell` from shells/index.  Phase 0 of the migration has
// every role mapped to DefaultShell so behavior is identical to the
// pre-refactor world.  Subsequent phases will introduce FleetShell,
// DispatchShell, SafetyShell etc. without touching this file.
import { pickShell } from './shells';
import { useRoleView } from './context/RoleViewContext';
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
// Phase 1 of the role-shell migration moved these pages out of role-
// named folders (pages/fleet/, pages/safety/, pages/dispatch/) into
// feature-named folders so the directory structure stops competing
// with role names.  URLs are unchanged.
const Vehicles         = lazyWithReload(() => import('./pages/vehicles/Vehicles'));
const VehicleDetail    = lazyWithReload(() => import('./pages/vehicles/VehicleDetail'));
const LiveMap          = lazyWithReload(() => import('./pages/live-map/LiveMap'));
const Alerts           = lazyWithReload(() => import('./pages/alerts/Alerts'));
const Geofences        = lazyWithReload(() => import('./pages/geofences/Geofences'));
const RoutesPage       = lazyWithReload(() => import('./pages/routes/Routes'));
const Scorecards       = lazyWithReload(() => import('./pages/driver-scorecards/Scorecards'));
const Events           = lazyWithReload(() => import('./pages/safety-events/Events'));
const Cameras          = lazyWithReload(() => import('./pages/cameras/Cameras'));
const Parking          = lazyWithReload(() => import('./pages/parking/Parking'));
const ReportsLayout    = lazyWithReload(() => import('./pages/reports/ReportsLayout'));
const Reports          = lazyWithReload(() => import('./pages/reports/Reports'));
const ScheduledReports = lazyWithReload(() => import('./pages/reports/ScheduledReports'));
const RiskSummary      = lazyWithReload(() => import('./pages/reports/RiskSummary'));
const DotBinder        = lazyWithReload(() => import('./pages/reports/DotBinder'));
const FuelCosts        = lazyWithReload(() => import('./pages/costs/FuelCosts'));
const CostPerMile      = lazyWithReload(() => import('./pages/costs/CostPerMile'));
const Maintenance      = lazyWithReload(() => import('./pages/maintenance/Tasks'));
const WorkOrders       = lazyWithReload(() => import('./pages/work-orders/WorkOrders'));
const WorkOrderForm    = lazyWithReload(() => import('./pages/work-orders/WorkOrderForm'));
const CostReports      = lazyWithReload(() => import('./pages/reports/CostReports'));
// Inspections page hosts both the submissions list AND the template
// editor (as tabs) — the editor is fleet's tool, not a separate admin
// page.  Loaded as a single chunk.
const Inspections      = lazyWithReload(() => import('./pages/inspections/Inspections'));
const KnowledgeBase    = lazyWithReload(() => import('./pages/knowledge/KnowledgeBase'));
const TeamManagement   = lazyWithReload(() => import('./pages/admin/TeamManagement'));
const Companies        = lazyWithReload(() => import('./pages/admin/Companies'));
const AuditLog         = lazyWithReload(() => import('./pages/admin/AuditLog'));
const Settings         = lazyWithReload(() => import('./pages/admin/Settings'));
const Profile          = lazyWithReload(() => import('./pages/Profile'));
const MyNotifications  = lazyWithReload(() => import('./pages/MyNotifications'));
const Storage          = lazyWithReload(() => import('./pages/admin/Storage'));
const WorkHours        = lazyWithReload(() => import('./pages/admin/WorkHours'));
const Invites          = lazyWithReload(() => import('./pages/admin/Invites'));
const RolePermissions  = lazyWithReload(() => import('./pages/admin/RolePermissions'));
const ScorecardRules   = lazyWithReload(() => import('./pages/admin/ScorecardRules'));
const Billing          = lazyWithReload(() => import('./pages/admin/Billing'));
const Payroll          = lazyWithReload(() => import('./pages/payroll/Payroll'));
const Coaching         = lazyWithReload(() => import('./pages/coaching/Coaching'));
const Drivers          = lazyWithReload(() => import('./pages/workforce/Drivers'));
const AIChat           = lazyWithReload(() => import('./features/ai/Chat'));
const AISummary        = lazyWithReload(() => import('./features/ai/Summary'));
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
// shell's main column rather than swapping the whole shell.
function L(node: ReactNode) {
  return <Suspense fallback={<RouteSkeleton />}>{node}</Suspense>;
}

export default function AppRouter() {
  // pickShell looks up the right top-level wrapper for the active
  // persona (Owner/Admin → DefaultShell; future phases override per
  // role).  Resolved once per render of AppRouter; switching persona
  // re-renders this component because RoleViewContext updates.
  const { activeView } = useRoleView();
  const Shell = pickShell(activeView);
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={L(<Overview />)} />

        {/* Shared feature pages.  URL = feature name (single source of
            truth); the persona context is carried by the subdomain
            (fleet.4truck.us etc.) and the active shell, never the URL
            path. */}
        <Route path="live-map" element={L(<P perm={['can_location_map', 'can_location_own']}><LiveMap /></P>)} />
        <Route path="vehicles" element={L(<P perm={['can_vehicle_all', 'can_vehicle_own']}><Vehicles /></P>)} />
        <Route path="vehicles/:name" element={L(<P perm={['can_vehicle_all', 'can_vehicle_own']}><VehicleDetail /></P>)} />
        <Route path="routes" element={L(<P perm={['can_route_all', 'can_route_own']}><RoutesPage /></P>)} />
        <Route path="geofences" element={L(<P perm={['can_geofence_all', 'can_geofence_own']}><Geofences /></P>)} />
        <Route path="parking" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Parking /></P>)} />
        <Route path="alerts" element={L(<P perm={['can_alerts_all', 'can_alerts_own']}><Alerts /></P>)} />
        <Route path="driver-scorecards" element={L(<P perm={['can_scorecard_all', 'can_scorecard_own']}><Scorecards /></P>)} />
        <Route path="safety-events" element={L(<P perm={['can_events_all', 'can_events_own']}><Events /></P>)} />
        <Route path="cameras" element={L(<P perm="can_faults"><Cameras /></P>)} />

        {/* AI Assistant — gate aligned with sidebar + backend so any
            user who sees the link can also load the page.  Any of the
            three flags grants access: can_faults (faults-Q&A),
            can_vehicle_all (cross-fleet), can_vehicle_own (driver
            self-view).  Keeps the AI useful for non-fault personas
            (HR, Dispatch, Accounting) without inheriting fault data. */}
        <Route path="ai/chat" element={L(<P perm={['can_faults', 'can_vehicle_all', 'can_vehicle_own']}><AIChat /></P>)} />
        <Route path="ai/summary" element={L(<P perm={['can_faults', 'can_vehicle_all', 'can_vehicle_own']}><AISummary /></P>)} />

        {/* Reports module — the four sub-pages are nested under one
            ReportsLayout that owns the shared header + cross-page
            sub-nav.  The parent route gates on the UNION of child
            flags so a user with access to ANY sub-page sees the
            module; per-tab visibility inside the layout filters the
            sub-nav by the same flags. */}
        <Route
          path="reports"
          element={L(
            <P perm={[
              'can_faults', 'can_risk_report_all', 'can_risk_report_own',
              'can_cost_reports', 'can_digest', 'can_maintenance_all',
            ]}><ReportsLayout /></P>
          )}
        >
          <Route index             element={L(<P perm="can_faults"><Reports /></P>)} />
          <Route path="risk-summary"     element={L(<P perm={['can_risk_report_all', 'can_risk_report_own']}><RiskSummary /></P>)} />
          <Route path="cost-reports"     element={L(<P perm="can_cost_reports"><CostReports /></P>)} />
          <Route path="dot-binder"        element={L(<P perm="can_maintenance_all"><DotBinder /></P>)} />
          <Route path="scheduled-reports" element={L(<P perm="can_digest"><ScheduledReports /></P>)} />
        </Route>
        {/* Legacy paths — bookmarks/links from before the
            URL canonicalisation land here and redirect cleanly. */}
        <Route path="reports/subscriptions" element={<Navigate to="/reports/scheduled-reports" replace />} />
        <Route path="cost-reports" element={<Navigate to="/reports/cost-reports" replace />} />

        {/* Costs */}
        <Route path="costs/fuel" element={L(<P perm="can_fuel_cost"><FuelCosts /></P>)} />
        <Route path="costs/cpm" element={L(<P perm="can_cost_per_mile"><CostPerMile /></P>)} />

        {/* Maintenance */}
        <Route path="maintenance" element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><Maintenance /></P>)} />

        {/* PTI (Pre-Trip Inspections) — fleet review surface.
            Drivers complete inspections via the Mini App; this page
            is the dashboard counterpart for the review queue. */}
        <Route path="inspections" element={L(<P perm="can_inspections_all"><Inspections /></P>)} />

        {/* Work Orders — separate module from Maintenance.  Maintenance
            tracks "what needs doing"; Work Orders is "what was done"
            (shop visits, costs, parts, attachments). */}
        <Route path="work-orders"         element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><WorkOrders /></P>)} />
        <Route path="work-orders/new"     element={L(<P perm="can_maintenance_all"><WorkOrderForm /></P>)} />
        <Route path="work-orders/:id"     element={L(<P perm={['can_maintenance_all', 'can_maintenance_own']}><WorkOrderForm /></P>)} />
        {/* Cost Reports route lives under /reports/* (see above) since
            it's a sub-page of the Reports module; this position kept
            empty intentionally — the legacy /cost-reports redirect
            handles in-flight bookmarks. */}

        {/* Knowledge Base */}
        <Route path="knowledge" element={L(<KnowledgeBase />)} />

        {/* Admin */}
        <Route path="admin/users" element={L(<P perm="can_manage_users"><TeamManagement /></P>)} />
        <Route path="admin/companies" element={L(<P perm="can_manage_companies"><Companies /></P>)} />
        <Route path="admin/audit" element={L(<P perm="can_manage_users"><AuditLog /></P>)} />
        <Route path="admin/work-hours" element={L(<P perm="can_manage_account"><WorkHours /></P>)} />
        <Route path="admin/invites" element={L(<P perm="can_invite"><Invites /></P>)} />
        <Route path="admin/settings" element={L(<P perm="can_manage_account"><Settings /></P>)} />
        {/* Personal preferences — accessible to every authenticated
            user regardless of role. */}
        <Route path="profile" element={L(<Profile />)} />
        <Route path="notifications" element={L(<MyNotifications />)} />
        <Route path="admin/storage"  element={L(<P perm="can_manage_account"><Storage /></P>)} />
        <Route path="admin/permissions" element={L(<P perm="can_manage_account"><RolePermissions /></P>)} />
        <Route path="admin/scorecard-rules" element={L(<P perm="can_manage_account"><ScorecardRules /></P>)} />
        {/* Legacy admin alias — the template editor moved INSIDE the
            Inspections page (it's a fleet-ops responsibility, not an
            account setting).  Keep the URL as a redirect so existing
            bookmarks still work. */}
        <Route
          path="admin/inspection-template"
          element={<Navigate to="/inspections?tab=template" replace />}
        />
        <Route path="admin/billing" element={L(<P perm="can_manage_billing"><Billing /></P>)} />
        <Route path="payroll" element={L(<P perm="can_payroll_admin"><Payroll /></P>)} />
        <Route path="coaching" element={L(<P perm="can_coaching_admin"><Coaching /></P>)} />
        <Route path="workforce/drivers" element={L(<P perm="can_manage_driver_docs"><Drivers /></P>)} />
        <Route path="*" element={L(<NotFound />)} />
      </Route>
    </Routes>
  );
}
