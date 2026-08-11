/**
 * /kpi — the entry that follows the active role view.
 *
 * KPI is not one page: each role's KPI is its own section page
 * (/kpi/dispatch today; vehicles / safety / drivers as they land).
 * This entry sends the viewer to THEIR section — a dispatcher lands on
 * Dispatch KPI, the Fleet view will land on Vehicles — the same
 * mechanism the role shells use.  Owners walk sections with the
 * switcher in each section's header.
 */
import { Navigate } from 'react-router-dom';
import { useRoleView } from '../../context/RoleViewContext';
import { viewSection } from './sections';

export default function KpiEntry() {
  const { activeView } = useRoleView();
  return <Navigate to={viewSection(activeView).path} replace />;
}
