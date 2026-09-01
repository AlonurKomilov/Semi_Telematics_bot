/**
 * Reports module shell — shared chrome for every page under
 * ``/reports/*``.  Renders:
 *
 *   1. A single ``<PageHeader>`` with the module title + description.
 *      Children DON'T render their own header; they get a clean
 *      content area to focus on the data + sub-tabs they own.
 *
 *   2. A cross-page sub-navigation strip (Reports | Risk Summary |
 *      Cost Reports | Scheduled Reports) so a user moving between
 *      the four pages doesn't have to bounce back to the sidebar.
 *      Tabs are filtered by the user's permission flags — a user
 *      without ``can_risk_report_*`` doesn't see the Risk Summary tab.
 *
 *   3. ``<Outlet/>`` for the child page content.
 *
 *   4. ``<Outlet context={...}/>`` exposes a ``setActions`` setter to
 *      child pages so each one can render its own header actions
 *      (download buttons, period selectors, etc.) into the layout's
 *      action slot without breaking the shared chrome.
 *
 * The Reports landing (faults / fuel / health / efficiency tabs) is
 * the index child of this layout (``router.tsx``).
 */
import { useState, useMemo, type ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FileText, Mail, TrendingUp, AlertTriangle, Shield } from 'lucide-react';
import { PageHeader } from '../../components/shell';
import { useAuth } from '../../context/AuthContext';

interface SubNavEntry {
  /** Route segment relative to the layout root (``/reports``).  Empty
   *  string = the index route (Reports landing). */
  to: string;
  label: string;
  icon: typeof FileText;
  /** Permission keys to gate this tab.  ANY-of semantics — the tab
   *  shows if the user has at least one listed permission.  ``null``
   *  means "show to everyone with at least one other reports tab". */
  perms: string[] | null;
}

const SUB_NAV: SubNavEntry[] = [
  // Reports landing — gated on can_faults today (faults is the
  // default tab); fuel/health/efficiency tabs filter further at the
  // page level.  Hide the tab for personas without can_faults.
  { to: '',                 label: 'Reports',           icon: FileText,    perms: ['can_faults'] },
  { to: 'risk-summary',     label: 'Risk Summary',      icon: AlertTriangle, perms: ['can_risk_report_all', 'can_risk_report_own'] },
  { to: 'cost-reports',     label: 'Cost Reports',      icon: TrendingUp,  perms: ['can_cost_reports'] },
  // DOT Binder — gated on can_maintenance_all (managers only).
  // Moved here from the Maintenance page in 2026-06 because it's
  // conceptually a stakeholder-facing audit PDF like Risk Summary,
  // not an operational task editor; see
  // docs/architecture/reports-hierarchy-audit.md.
  { to: 'dot-binder',       label: 'DOT Binder',        icon: Shield,      perms: ['can_manage_maintenance'] },
  { to: 'scheduled-reports', label: 'Scheduled Reports', icon: Mail,       perms: ['can_digest'] },
];


export interface ReportsLayoutOutletContext {
  /** Child pages call this to inject their own action chrome
   *  (download buttons, period selector, etc.) into the layout header
   *  action slot.  Pass ``null`` (or call with no value on unmount)
   *  to clear. */
  setActions: (actions: ReactNode | null) => void;
}


export default function ReportsLayout() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [actions, setActions] = useState<ReactNode | null>(null);

  const visibleTabs = useMemo(() => {
    const perms = user?.permissions ?? null;
    if (!perms) return SUB_NAV;
    return SUB_NAV.filter((tab) => {
      if (!tab.perms) return true;
      return tab.perms.some((p) => Boolean(perms[p]));
    });
  }, [user?.permissions]);

  return (
    <div>
      <PageHeader
        icon={FileText}
        title={t('pages.reports_module_title', { defaultValue: 'Reports' })}
        description={t('pages.reports_module_desc', {
          defaultValue: 'View, schedule, and download fleet reports — on-demand exports plus recurring delivery.',
        })}
        actions={actions ?? undefined}
      />

      {visibleTabs.length > 1 && (
        <div className="mb-6 flex flex-wrap gap-1 border-b border-border">
          {visibleTabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <NavLink
                key={tab.to || 'index'}
                to={tab.to || '.'}
                end={tab.to === ''}
                className={({ isActive }) =>
                  `inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                    isActive
                      ? 'border-primary text-primary'
                      : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                  }`
                }
              >
                <Icon className="size-4" />
                {tab.label}
              </NavLink>
            );
          })}
        </div>
      )}

      <Outlet context={{ setActions } satisfies ReportsLayoutOutletContext} />
    </div>
  );
}
