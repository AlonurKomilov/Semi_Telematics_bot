/**
 * Accounting persona sidebar — money-management navigation.
 *
 * An accountant's day is driven by billing, fuel & maintenance cost
 * tracking, payroll, and financial reports.  Their navigation puts a
 * "Costs" group at the top (using the existing ``reports_group``
 * label since fuel/CPM/cost-reports live alongside reports
 * structurally), surfaces Billing and Payroll prominently, and trims
 * everything else.  Read-only Vehicles stays in a separate group for
 * asset accounting context.
 *
 * Permission filtering still runs on top — see capabilities/iam/
 * permissions.py for the ACCOUNTING FeatureSet.
 */
import {
  LayoutDashboard, Truck, Bot, BookOpen, FileText, Mail,
  Fuel, DollarSign, TrendingUp, CreditCard,
} from 'lucide-react';
import type { NavGroup } from './defaultNav';

export const accountingNav: NavGroup[] = [
  {
    titleKey: null,
    items: [
      { labelKey: 'nav.overview',     path: '/',        icon: LayoutDashboard, permission: null },
      { labelKey: 'nav.ai_assistant', path: '/ai/chat', icon: Bot,             permission: ['can_faults', 'can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    // Costs & payroll — accounting's primary working set.  Lives in
    // its own ``costs_group`` so the label matches the URLs
    // (/costs/* + /payroll) and the Reports group stays semantically
    // clean (only items physically under /reports/*).  Cost Reports
    // moved out into the Reports module shell — accounting reaches it
    // via the Reports sub-nav.
    titleKey: 'nav.costs_group',
    items: [
      { labelKey: 'nav.fuel_costs',    path: '/costs/fuel',   icon: Fuel,       permission: ['can_fuel_cost'] },
      { labelKey: 'nav.cost_per_mile', path: '/costs/cpm',    icon: DollarSign, permission: ['can_cost_per_mile'] },
      { labelKey: 'nav.payroll',       path: '/payroll',      icon: CreditCard, permission: ['can_payroll_admin'] },
    ],
  },
  {
    // Reports module shell — single sidebar entry; sub-pages live as
    // tabs inside ReportsLayout.tsx (Reports / Risk Summary / Cost
    // Reports / Scheduled Reports).
    titleKey: 'nav.reports_group',
    items: [
      { labelKey: 'nav.reports', path: '/reports', icon: FileText,
        permission: ['can_faults', 'can_risk_report_all', 'can_risk_report_own', 'can_cost_reports', 'can_digest'] },
    ],
  },
  {
    // Read-only Fleet — accounting needs WHICH ASSETS generate WHICH
    // costs.  No Live Map / Maintenance — those are Fleet's domain.
    titleKey: 'nav.fleet_group',
    items: [
      { labelKey: 'nav.vehicles', path: '/vehicles', icon: Truck, permission: ['can_vehicle_all', 'can_vehicle_own'] },
    ],
  },
  {
    titleKey: null,
    items: [
      // Billing belongs here — accounting owns the billing
      // relationship with platform.  Lives under /admin/billing so
      // path is unchanged; perm gate (can_manage_billing) lets the
      // account-admin pages still gate it identically.
      { labelKey: 'nav.billing',        path: '/admin/billing', icon: CreditCard, permission: ['can_manage_billing'] },
      { labelKey: 'nav.knowledge_base', path: '/knowledge',     icon: BookOpen,   permission: null },
    ],
  },
  // NOTE: account_admin group, Workforce, Safety, Maintenance, Work
  // Orders, Inspections all intentionally omitted.  Accounting
  // doesn't manage drivers / vehicles / incidents — they review the
  // financial outcome of those decisions.
];
