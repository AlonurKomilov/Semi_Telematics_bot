/**
 * Uniform props every Overview section accepts.
 *
 * The page wrapper does the single ``['dashboard-stats']`` fetch once
 * and hands the result (plus the few page-level imperative handles —
 * navigate, has, refresh state) down to every section via the
 * sectionProps bag.  Each section destructures what it needs and
 * ignores the rest.
 *
 * Keeping a single shared fetch (vs per-section useQuery) is the
 * right call here because /overview/stats returns the whole
 * KPI bundle in one shot — there's no value in N small requests when
 * the backend already returns everything together.
 */
import type { NavigateFunction } from 'react-router-dom';
import type { DashboardStats } from '../../../../types';
import type { HeaderConfig, KpiKey } from '../../personaConfig';

export interface OverviewSectionProps {
  stats: DashboardStats;
  navigate: NavigateFunction;
  has: (flag: string) => boolean;
  /** ``has`` AND the member's unit width is 'all' — for links whose
   * target route is ``require_wide`` (see config/unitWidth.ts). */
  hasWide: (flag: string) => boolean;
  greetingName: string;
  /** When the data was last fetched — for the LastUpdated chip in OverviewHeader. */
  fetchedAt?: number;
  /** True while a refetch is in flight — drives the spinner in OverviewHeader. */
  isFetching: boolean;
  /** Imperative refetch trigger — wired to the LastUpdated "Refresh" button. */
  refetch: () => void;
  /**
   * Pre-resolved persona-aware presentation config.  Computed by the
   * Overview page wrapper from ``personaConfig.ts``; sections render
   * what they're handed and never look up persona themselves —
   * enforced by ``check-role-drift.mjs``.
   */
  headerConfig: HeaderConfig;
  /** Pre-resolved drill-down KPI priority for the active persona. */
  kpiPriority: ReadonlyArray<KpiKey>;
}
