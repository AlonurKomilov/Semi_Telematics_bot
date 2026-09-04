/**
 * Single source of truth for the recurring report catalogue (dashboard side).
 *
 * Mirrors `capabilities/reporting/registry.py`.  Keys MUST match the
 * Python side exactly — the API and bot scheduler use the same strings
 * in `digest_subscriptions.report_type` and the `/api/reports/export`
 * query param.
 *
 * If you add a report, edit BOTH files.  A dual-side test could enforce
 * parity in the future; for now keep an eye on it in code review.
 */

export interface ReportSpec {
  /** Stable identifier shared with the Python registry + API. */
  key: 'faults' | 'fuel' | 'health' | 'efficiency' | 'camera';
  emoji: string;
  /** Short label for dashboard tabs (no emoji, no qualifier). */
  labelShort: string;
  /** Full label for menus where the short form is ambiguous. */
  labelFull: string;
  /** Permission flag gating the API export endpoint. */
  permission: string;
  /** True when the report has a generic API export path
   *  (`/api/reports/export`).  Camera Check is the one exception. */
  hasApiExport: boolean;
}

export const REPORTS: readonly ReportSpec[] = [
  { key: 'faults',     emoji: '🔧', labelShort: 'Faults',     labelFull: 'Faults',         permission: 'can_view_faults',     hasApiExport: true },
  { key: 'fuel',       emoji: '⛽', labelShort: 'Fuel & DEF', labelFull: 'Fuel & DEF',     permission: 'can_view_fuel',       hasApiExport: true },
  { key: 'health',     emoji: '🏥', labelShort: 'Health',     labelFull: 'Vehicle Health', permission: 'can_view_health',     hasApiExport: true },
  { key: 'efficiency', emoji: '📊', labelShort: 'Efficiency', labelFull: 'Efficiency',     permission: 'can_view_efficiency', hasApiExport: true },
  { key: 'camera',     emoji: '📷', labelShort: 'Cameras',    labelFull: 'Camera Check',   permission: 'can_digest',     hasApiExport: false },
] as const;

export type ReportKey = ReportSpec['key'];

export const REPORTS_BY_KEY: Record<ReportKey, ReportSpec> =
  REPORTS.reduce((acc, r) => {
    acc[r.key] = r;
    return acc;
  }, {} as Record<ReportKey, ReportSpec>);

export function getReport(key: string): ReportSpec | undefined {
  return REPORTS_BY_KEY[key as ReportKey];
}

/** Returns "🔧 Faults" — convenient for menus + buttons. */
export function reportLabelWithEmoji(key: string): string {
  const r = getReport(key);
  if (!r) return key;
  return `${r.emoji} ${r.labelFull}`;
}
