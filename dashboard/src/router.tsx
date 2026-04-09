import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import Overview from './pages/Overview';
import Vehicles from './pages/fleet/Vehicles';
import VehicleDetail from './pages/fleet/VehicleDetail';
import LiveMap from './pages/fleet/LiveMap';
import Alerts from './pages/dispatch/Alerts';
import Geofences from './pages/dispatch/Geofences';
import RoutesPage from './pages/dispatch/Routes';
import Scorecards from './pages/safety/Scorecards';
import Events from './pages/safety/Events';
import Cameras from './pages/safety/Cameras';
import Reports from './pages/reports/Reports';
import FuelCosts from './pages/costs/FuelCosts';
import CostPerMile from './pages/costs/CostPerMile';
import Maintenance from './pages/maintenance/Tasks';
import Users from './pages/admin/Users';
import Companies from './pages/admin/Companies';
import AuditLog from './pages/admin/AuditLog';
import Settings from './pages/admin/Settings';
import NotFound from './pages/NotFound';
import type { ReactNode } from 'react';

function P({ perm, children }: { perm: string | string[]; children: ReactNode }) {
  return <ProtectedRoute permission={perm}>{children}</ProtectedRoute>;
}

export default function AppRouter() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />

        {/* Fleet */}
        <Route path="fleet/vehicles" element={<P perm={['can_truck_all', 'can_truck_own']}><Vehicles /></P>} />
        <Route path="fleet/vehicle/:name" element={<P perm={['can_truck_all', 'can_truck_own']}><VehicleDetail /></P>} />
        <Route path="fleet/map" element={<P perm={['can_location_map', 'can_location_own']}><LiveMap /></P>} />

        {/* Dispatch */}
        <Route path="dispatch/alerts" element={<P perm={['can_alerts_all', 'can_alerts_own']}><Alerts /></P>} />
        <Route path="dispatch/geofences" element={<P perm={['can_geofence_all', 'can_geofence_own']}><Geofences /></P>} />
        <Route path="dispatch/routes" element={<P perm={['can_route_all', 'can_route_own']}><RoutesPage /></P>} />

        {/* Safety */}
        <Route path="safety/scorecards" element={<P perm={['can_scorecard_all', 'can_scorecard_own']}><Scorecards /></P>} />
        <Route path="safety/events" element={<P perm={['can_events_all', 'can_events_own']}><Events /></P>} />
        <Route path="safety/cameras" element={<P perm="can_faults"><Cameras /></P>} />

        {/* Reports */}
        <Route path="reports" element={<P perm="can_faults"><Reports /></P>} />

        {/* Costs */}
        <Route path="costs/fuel" element={<P perm="can_fuel_cost"><FuelCosts /></P>} />
        <Route path="costs/cpm" element={<P perm="can_cost_per_mile"><CostPerMile /></P>} />

        {/* Maintenance */}
        <Route path="maintenance" element={<P perm={['can_maintenance_all', 'can_maintenance_own']}><Maintenance /></P>} />

        {/* Admin */}
        <Route path="admin/users" element={<P perm="can_manage_users"><Users /></P>} />
        <Route path="admin/companies" element={<P perm="can_manage_companies"><Companies /></P>} />
        <Route path="admin/audit" element={<P perm="can_manage_users"><AuditLog /></P>} />
        <Route path="admin/settings" element={<P perm="can_manage_account"><Settings /></P>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
