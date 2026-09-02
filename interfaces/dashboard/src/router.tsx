import { Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense, type ReactNode } from 'react';
// Shell selection: rather than one hardcoded Layout, the router asks
// :func:`pickShell` (shells/index) which shell the active persona
// renders in. Adding or retiring a persona shell never touches this
// file — the registry is the only place that mapping lives.
import { pickShell } from './shells';
import { MODS_HREF } from './mods';
import { useRoleView } from './context/RoleViewContext';
import ProtectedRoute from './components/ProtectedRoute';
import AssistantHost from './features/ai/AssistantHost';

/**
 * Wrap ``lazy(() => import(...))`` so a "chunk-not-found" failure
 * (deploy rotated the Vite content hashes while the user's
 * index.html was still cached client-side) triggers a one-shot hard
 * reload instead of leaving the SPA wedged on a white screen with
 * ``Failed to fetch dynamically imported module``.
 *
 * The loop guard is TIME-BOXED, not once-per-session.  It used to read
 * the stored value as a mere boolean — so the first stale chunk in a
 * session reloaded, and every stale chunk after that re-threw and left
 * the SPA wedged on exactly the error this wrapper exists to prevent.
 * That is not a rare case: any session spanning two deploys hits it, and
 * a session spanning several (an afternoon of frontend work) hits it
 * repeatedly.  The value was already a timestamp; now it is actually
 * compared against.
 *
 * A genuine reload loop retries within milliseconds, while a later
 * deploy is minutes away — so a short window separates the two cleanly.
 */
const CHUNK_RELOAD_KEY = 'chunk-reload-attempted';
/** Below this, a repeat failure means the reload itself is not helping. */
const CHUNK_RELOAD_LOOP_WINDOW_MS = 10_000;
// Components have heterogeneous props (most take none, Chat takes an
// optional `variant`), so this router helper is intentionally prop-
// agnostic via `any` — the concrete prop types are enforced at each
// render site, not here.
function lazyWithReload<T extends { default: React.ComponentType<any> }>(
  loader: () => Promise<T>,
): React.LazyExoticComponent<React.ComponentType<any>> {
  return lazy(() =>
    loader().catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      const isChunkLoadError =
        msg.includes('Failed to fetch dynamically imported module') ||
        msg.includes('Importing a module script failed') ||
        msg.includes('error loading dynamically imported module');
      const last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) ?? 0);
      // NaN/0 (never reloaded, or a legacy non-numeric value) reads as
      // "long ago", which is the safe direction: recover rather than wedge.
      const inLoopWindow =
        Number.isFinite(last) && last > 0
        && Date.now() - last < CHUNK_RELOAD_LOOP_WINDOW_MS;
      if (isChunkLoadError && !inLoopWindow) {
        sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
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
const Overview         = lazyWithReload(() => import('./features/overview/Overview'));
// Phase 1 of the role-shell migration moved these pages out of role-
// named folders (pages/fleet/, pages/safety/, pages/dispatch/) into
// feature-named folders so the directory structure stops competing
// with role names.  URLs are unchanged.
const Vehicles         = lazyWithReload(() => import('./features/vehicles/Vehicles'));
const VehicleDetail    = lazyWithReload(() => import('./features/vehicles/VehicleDetail'));
const VehicleInventory = lazyWithReload(() => import('./features/vehicles/inventory/InventoryPage'));
const VehicleDocuments = lazyWithReload(() => import('./features/vehicles/documents/DocumentsPage'));
const LiveMap          = lazyWithReload(() => import('./features/live-map/LiveMap'));
const Alerts           = lazyWithReload(() => import('./features/alerts/Alerts'));
const Geofences        = lazyWithReload(() => import('./features/geofences/Geofences'));
const RoutesPage       = lazyWithReload(() => import('./features/routes/Routes'));
const Scorecards       = lazyWithReload(() => import('./features/scorecards/Scorecards'));
const Events           = lazyWithReload(() => import('./features/safety-events/Events'));
const Cameras          = lazyWithReload(() => import('./features/cameras/Cameras'));
const Parking          = lazyWithReload(() => import('./features/parking/Parking'));
const ReportsLayout    = lazyWithReload(() => import('./features/reports/ReportsLayout'));
const Reports          = lazyWithReload(() => import('./features/reports/Reports'));
const ScheduledReports = lazyWithReload(() => import('./features/reports/ScheduledReports'));
const RiskSummary      = lazyWithReload(() => import('./features/reports/RiskSummary'));
const DotBinder        = lazyWithReload(() => import('./features/reports/DotBinder'));
const FuelCosts        = lazyWithReload(() => import('./features/costs/FuelCosts'));
const CostPerMile      = lazyWithReload(() => import('./features/costs/CostPerMile'));
const Maintenance      = lazyWithReload(() => import('./features/maintenance/Tasks'));
const WorkOrders       = lazyWithReload(() => import('./features/work-orders/WorkOrders'));
const Loads            = lazyWithReload(() => import('./features/loads/Loads'));
const KpiEntry         = lazyWithReload(() => import('./features/kpi/KpiEntry'));
const MyPayouts        = lazyWithReload(() => import('./features/kpi/dispatch/MyPayouts'));
const KpiConfiguration = lazyWithReload(() => import('./features/kpi/config/KpiConfiguration'));
const IncentiveRuns = lazyWithReload(() => import('./features/kpi/dispatch/IncentiveRuns'));
const WorkOrderForm    = lazyWithReload(() => import('./features/work-orders/WorkOrderForm'));
const Vendors          = lazyWithReload(() => import('./features/vendors/Vendors'));
const VendorProfile    = lazyWithReload(() => import('./features/vendors/VendorProfile'));
const Parts            = lazyWithReload(() => import('./features/parts/Parts'));
const ServiceTasks     = lazyWithReload(() => import('./features/service-tasks/ServiceTasks'));
const ServiceTaskDetail = lazyWithReload(() => import('./features/service-tasks/ServiceTaskDetail'));
const PartDetail       = lazyWithReload(() => import('./features/parts/PartDetail'));
const CostReports      = lazyWithReload(() => import('./features/reports/CostReports'));
// Inspections page hosts both the submissions list AND the template
// editor (as tabs) — the editor is fleet's tool, not a separate admin
// page.  Loaded as a single chunk.
const Inspections      = lazyWithReload(() => import('./features/inspections/Inspections'));
const TruckAnatomy     = lazyWithReload(() => import('./features/truck-anatomy/TruckAnatomy'));
const KnowledgeBase    = lazyWithReload(() => import('./features/knowledge/KnowledgeBase'));
const ToursPage        = lazyWithReload(() => import('./features/tours/ToursPage'));
const TeamManagement   = lazyWithReload(() => import('./features/settings/TeamManagement'));
const Companies        = lazyWithReload(() => import('./features/settings/Companies'));
const Integrations     = lazyWithReload(() => import('./features/integrations/Integrations'));
const AuditLog         = lazyWithReload(() => import('./features/settings/AuditLog'));
const Settings         = lazyWithReload(() => import('./features/settings/Settings'));
const Profile          = lazyWithReload(() => import('./pages/Profile'));
const MyNotifications  = lazyWithReload(() => import('./features/alerts/MyNotifications'));
const NotificationCenter = lazyWithReload(() => import('./features/alerts/NotificationCenter'));
const GroupDelivery    = lazyWithReload(() => import('./features/alerts/GroupDelivery'));
const MyTriggers       = lazyWithReload(() => import('./features/alerts/MyTriggers'));
const Storage          = lazyWithReload(() => import('./features/object-storage/ObjectStorage'));
const WorkHours        = lazyWithReload(() => import('./features/settings/WorkHours'));
const Invites          = lazyWithReload(() => import('./features/settings/Invites'));
const Permissions      = lazyWithReload(() => import('./features/permissions/Permissions'));
const ScorecardRules   = lazyWithReload(() => import('./features/scorecards/ScorecardRules'));
const Billing          = lazyWithReload(() => import('./features/billing/Billing'));
const DriverPay        = lazyWithReload(() => import('./features/driver_pay/DriverPay'));
const Coaching         = lazyWithReload(() => import('./features/coaching/Coaching'));
const Drivers          = lazyWithReload(() => import('./features/drivers/Drivers'));
const Applications     = lazyWithReload(() => import('./features/applications/Applications'));
const ApplyPreview     = lazyWithReload(() => import('./features/applications/ApplyPreview'));
const CarrierDirectory = lazyWithReload(() => import('./features/carrier-directory/CarrierDirectory'));
const CarrierProfile   = lazyWithReload(() => import('./features/carrier-directory/CarrierProfile'));
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
  // Resolved once per render of AppRouter; switching persona re-renders
  // this component because RoleViewContext updates.
  const { activeView } = useRoleView();
  const Shell = pickShell(activeView);
  return (
    <Routes>
      {/* Full-screen recruiter preview of the public apply form — sits
          OUTSIDE the dashboard shell (no sidebar/chrome) so it renders
          exactly like the real form, but still auth + permission gated. */}
      <Route path="applications/preview/:companyId"
        element={L(<P perm="can_manage_applications"><ApplyPreview /></P>)} />
      {/* AssistantHost wraps the shell ONCE: it mounts the copilot panel
          + page-context providers above every dashboard page, so the
          panel is a single instance and feature pages can publish their
          context.  Wrapping is transparent to routing — Shell still
          renders <Outlet/> for the child routes below. */}
      <Route element={<AssistantHost><Shell /></AssistantHost>}>
        <Route index element={L(<Overview />)} />

        {/* Shared feature pages.  URL = feature name (single source of
            truth); the persona context is carried by the subdomain
            (fleet.4truck.us etc.) and the active shell, never the URL
            path. */}
        <Route path="live-map" element={L(<P perm={['can_view_location']}><LiveMap /></P>)} />
        <Route path="vehicles" element={L(<P perm={['can_view_vehicles']}><Vehicles /></P>)} />
        <Route path="vehicles/inventory" element={L(<P perm={['can_view_vehicles']}><VehicleInventory /></P>)} />
        {/* Before the parametric vehicle route for the same reason the
            API mounts its list first — otherwise "documents" resolves
            as a truck name. */}
        <Route path="vehicles/documents" element={L(<P perm={['can_vehicle_docs']}><VehicleDocuments /></P>)} />
        <Route path="vehicles/:name" element={L(<P perm={['can_view_vehicles']}><VehicleDetail /></P>)} />
        <Route path="routes" element={L(<P perm={['can_view_routes']}><RoutesPage /></P>)} />
        <Route path="geofences" element={L(<P perm={['can_view_geofence']}><Geofences /></P>)} />
        <Route path="parking" element={L(<P perm="can_view_vehicles"><Parking /></P>)} />
        <Route path="alerts" element={L(<P perm="can_view_vehicles"><Alerts /></P>)} />
        <Route path="scorecards" element={L(<P perm="can_view_scorecards"><Scorecards /></P>)} />
        <Route path="safety-events" element={L(<P perm={['can_view_events']}><Events /></P>)} />
        <Route path="cameras" element={L(<P perm="can_faults"><Cameras /></P>)} />

        {/* AI Assistant — gate on the SAME flag as the sidebar entry
            (featureCatalog `ai_assistant` → can_ai_chat), so any persona
            who sees the link can load the page.  can_ai_chat defaults True
            for every role; the AI's individual tools are permission-gated
            server-side, so a non-fleet persona (recruiter, HR, accounting)
            gets the assistant without inheriting fault/vehicle data.  The
            previous vehicle-centric guard bounced those personas to "/". */}
        <Route path="ai/chat" element={L(<P perm="can_ai_chat"><AIChat /></P>)} />
        <Route path="ai/summary" element={L(<P perm="can_ai_chat"><AISummary /></P>)} />

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
              'can_cost_reports', 'can_digest', 'can_manage_maintenance',
            ]}><ReportsLayout /></P>
          )}
        >
          <Route index             element={L(<P perm="can_faults"><Reports /></P>)} />
          <Route path="risk-summary"     element={L(<P perm={['can_risk_report_all', 'can_risk_report_own']}><RiskSummary /></P>)} />
          <Route path="cost-reports"     element={L(<P perm="can_cost_reports"><CostReports /></P>)} />
          <Route path="dot-binder"        element={L(<P perm="can_manage_maintenance"><DotBinder /></P>)} />
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
        <Route path="maintenance" element={L(<P perm="can_view_maintenance"><Maintenance /></P>)} />

        {/* PTI (Pre-Trip Inspections) — fleet review surface.
            Drivers complete inspections via the Mini App; this page
            is the dashboard counterpart for the review queue. */}
        <Route path="inspections" element={L(<P perm="can_manage_inspections"><Inspections /></P>)} />

        {/* Work Orders — separate module from Maintenance.  Maintenance
            tracks "what needs doing"; Work Orders is "what was done"
            (shop visits, costs, parts, attachments). */}
        <Route path="loads"               element={L(<P perm={['can_loads_all', 'can_loads_own']}><Loads /></P>)} />
        {/* /kpi follows the active role view to its section; each
            section is its own page + backend package (per-role KPI
            isolation — sections cannot affect each other). */}
        <Route path="kpi"                 element={L(<P perm="can_kpi"><KpiEntry /></P>)} />
        {/* The dispatch section's page IS the settlement surface (owner
            decision: one page, one flag).  /kpi/incentives below stays
            as an alias — bookmarks and auto-run notifications link it. */}
        <Route path="kpi/dispatch"        element={L(<P perm="can_kpi"><IncentiveRuns /></P>)} />
        {/* Config lives UNDER its section (KPI configuration · Dispatch);
            the old flat URL stays as a redirect for bookmarks. */}
        <Route path="kpi/dispatch/configuration" element={L(<P perm="can_manage_config_all"><KpiConfiguration /></P>)} />
        <Route path="kpi/configuration"   element={<Navigate to="/kpi/dispatch/configuration" replace />} />
        <Route path="kpi/incentives"      element={L(<P perm="can_kpi"><IncentiveRuns /></P>)} />
        {/* Self-scoped (own rows only) — authenticated, no can_* flag. */}
        <Route path="kpi/my-payouts"      element={L(<MyPayouts />)} />
        <Route path="work-orders"         element={L(<P perm="can_view_maintenance"><WorkOrders /></P>)} />
        {/* Manager-only: a vendor profile aggregates ALL trucks' work
            orders + account-wide spend — vehicle-scope users must not
            read it (their WO visibility is per-truck). */}
        <Route path="vendors"             element={L(<P perm="can_manage_work_orders"><Vendors /></P>)} />
        <Route path="vendors/:id"         element={L(<P perm="can_manage_work_orders"><VendorProfile /></P>)} />
        <Route path="parts"               element={L(<P perm="can_parts"><Parts /></P>)} />
        <Route path="service-tasks"       element={L(<P perm="can_service_tasks"><ServiceTasks /></P>)} />
        <Route path="service-tasks/:id"   element={L(<P perm="can_service_tasks"><ServiceTaskDetail /></P>)} />
        <Route path="parts/:id"           element={L(<P perm="can_parts"><PartDetail /></P>)} />
        <Route path="work-orders/new"     element={L(<P perm="can_manage_maintenance"><WorkOrderForm /></P>)} />
        <Route path="work-orders/:id"     element={L(<P perm="can_view_maintenance"><WorkOrderForm /></P>)} />
        {/* Cost Reports route lives under /reports/* (see above) since
            it's a sub-page of the Reports module; this position kept
            empty intentionally — the legacy /cost-reports redirect
            handles in-flight bookmarks. */}

        {/* Knowledge Base */}
        <Route path="knowledge" element={L(<KnowledgeBase />)} />
        {/* The tour library — browse + re-run every walkthrough. */}
        <Route path="tours" element={L(<ToursPage />)} />
        {/* Truck Anatomy — the taxonomy as a 3D learning model.  Gated
            by can_truck_anatomy: a DARK feature — seeded to nobody,
            the owner included — until granted per-account in the
            Permissions matrix.  Whole feature = features/truck-anatomy/
            + this line — removable as a unit. */}
        <Route path="truck-anatomy" element={L(<P perm="can_truck_anatomy"><TruckAnatomy /></P>)} />

        {/* Account / Settings pages — clean top-level paths (the
            meaningless /admin/* prefix was retired 2026-06-11).  Each
            old /admin/* URL keeps a redirect below so bookmarks survive. */}
        <Route path="team" element={L(<P perm="can_manage_users"><TeamManagement /></P>)} />
        <Route path="companies" element={L(<P perm="can_manage_companies"><Companies /></P>)} />
        <Route path="integrations" element={L(<P perm="can_manage_integrations"><Integrations /></P>)} />
        <Route path="audit" element={L(<P perm="can_manage_users"><AuditLog /></P>)} />
        <Route path="work-hours" element={L(<P perm="can_manage_work_hours"><WorkHours /></P>)} />
        <Route path="invites" element={L(<P perm="can_invite"><Invites /></P>)} />
        <Route path="settings" element={L(<P perm={["can_manage_account", "can_manage_role_bot"]}><Settings /></P>)} />
        {/* Personal preferences — accessible to every authenticated
            user regardless of role. */}
        <Route path="profile" element={L(<Profile />)} />
        {/* Mods is the ENGINE, not a page: the data lives in the user's
            preferences and the surface lives with it, on /profile. This
            route stays as a redirect so no link dies, and because /mods
            comes back as the CATALOGUE once there is content to browse.
            MODS_HREF is the one constant all three doors share. */}
        <Route path="mods" element={<Navigate to={MODS_HREF} replace />} />
        {/* Notifications are a cross-source PERSONAL surface on their own
            door (the topbar bell), not an Alerts sub-tab. /notifications =
            the browsable history (Notification center); /preferences = the
            settings page (the bell's gear). The alert BOARD stays at
            /alerts; the old /alerts/preferences path 301s so bookmarks
            survive. */}
        <Route path="notifications" element={L(<NotificationCenter />)} />
        <Route path="notifications/preferences" element={L(<MyNotifications />)} />
        <Route path="alerts/preferences" element={<Navigate to="/notifications/preferences" replace />} />
        <Route path="alerts/group-delivery" element={L(<GroupDelivery />)} />
        {/* Deliberately UNGATED, unlike the Board beside it: a trigger is
            self-scoped — it watches only the vehicles its owner can
            already see and delivers to nobody else — so requiring an
            alerts permission here would take the feature away from the
            people who reached it from notification preferences, where it
            used to live and where no permission gated it either. */}
        <Route path="alerts/triggers" element={L(<MyTriggers />)} />
        <Route path="object-storage" element={L(<P perm="can_manage_storage"><Storage /></P>)} />
        {/* bookmarks + the historical /admin/storage redirect land here */}
        <Route path="storage" element={<Navigate to="/object-storage" replace />} />
        <Route path="permissions" element={L(<P perm="can_manage_permissions"><Permissions /></P>)} />
        <Route path="scorecard-rules" element={L(<P perm="can_manage_config_all"><ScorecardRules /></P>)} />
        <Route path="billing" element={L(<P perm="can_manage_billing"><Billing /></P>)} />

        {/* Legacy /admin/* redirects — kept so existing bookmarks and
            deep links resolve.  /admin/modules folded into Permissions;
            /admin/inspection-template moved inside the Inspections page. */}
        <Route path="admin/users" element={<Navigate to="/team" replace />} />
        <Route path="admin/companies" element={<Navigate to="/companies" replace />} />
        <Route path="admin/integrations" element={<Navigate to="/integrations" replace />} />
        <Route path="admin/audit" element={<Navigate to="/audit" replace />} />
        <Route path="admin/work-hours" element={<Navigate to="/work-hours" replace />} />
        <Route path="admin/invites" element={<Navigate to="/invites" replace />} />
        <Route path="admin/settings" element={<Navigate to="/settings" replace />} />
        <Route path="admin/storage" element={<Navigate to="/storage" replace />} />
        <Route path="admin/permissions" element={<Navigate to="/permissions" replace />} />
        <Route path="admin/scorecard-rules" element={<Navigate to="/scorecard-rules" replace />} />
        <Route path="admin/billing" element={<Navigate to="/billing" replace />} />
        <Route path="admin/modules" element={<Navigate to="/permissions" replace />} />
        <Route
          path="admin/inspection-template"
          element={<Navigate to="/inspections?tab=template" replace />}
        />
        <Route path="driver-pay" element={L(<P perm="can_driver_pay_admin"><DriverPay /></P>)} />
        <Route path="coaching" element={L(<P perm="can_coaching_admin"><Coaching /></P>)} />
        <Route path="workforce/drivers" element={L(<P perm="can_manage_driver_docs"><Drivers /></P>)} />
        <Route path="workforce/applications" element={L(<P perm="can_manage_applications"><Applications /></P>)} />
        <Route path="workforce/carrier-directory" element={L(<P perm="can_carrier_directory"><CarrierDirectory /></P>)} />
        <Route path="workforce/carrier-directory/:id" element={L(<P perm="can_carrier_directory"><CarrierProfile /></P>)} />
        <Route path="*" element={L(<NotFound />)} />
      </Route>
    </Routes>
  );
}
