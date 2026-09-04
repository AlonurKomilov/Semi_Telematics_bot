/**
 * Overview page — Pattern B composition.
 *
 * Single shared query (``['dashboard-stats']``) fetches the whole KPI
 * bundle once; the page wrapper passes the result to every section
 * via PageLayoutHost's sectionProps.  Each section destructures only
 * what it needs and renders independently — sections that have
 * nothing to show (e.g. AISummary on an empty fleet) silently
 * no-op.
 *
 * Driver is the one persona that escapes the section model: the
 * driver path uses a single dedicated ``DriverOverview`` component
 * because "your one truck" doesn't decompose into reusable per-
 * persona sections.  The branch happens before PageLayoutHost is
 * even considered.
 */
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Truck } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  KpiSkeleton,
  ErrorState,
  Greeting,
} from '../../components/shell';
import type { DashboardStats } from '../../types';
import { PageLayoutHost } from '../_lib/PageLayoutHost';
import { OVERVIEW_SECTIONS } from './registry';
import { OVERVIEW_LAYOUTS } from './layouts';
import { resolveHeaderConfig, resolveKpiPriority } from './personaConfig';
import DriverOverview from './DriverOverview';

export default function Overview() {
  const navigate = useNavigate();
  const { has, hasWide } = useViewPermissions();
  const { user } = useAuth();
  // Page is the ONE place that reads persona; sections receive
  // pre-resolved config via sectionProps so they stay
  // persona-agnostic.  Enforced by check-role-drift.mjs.
  const { isDriver, persona } = useShellConfig();
  const greetingName = user?.display_name || '';
  const headerConfig = resolveHeaderConfig(persona);
  const kpiPriority = resolveKpiPriority(persona);

  const {
    data: stats,
    error: queryError,
    isLoading,
    isFetching,
    refetch,
    dataUpdatedAt,
  } = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: () => apiJSON<DashboardStats>('/overview/stats'),
  });
  const errorMsg =
    queryError instanceof Error
      ? queryError.message
      : queryError
        ? 'Failed to load'
        : '';

  if (isLoading && !stats) {
    return (
      <div>
        <Greeting name={greetingName} context="Loading current status…" />
        <PageHeader icon={Truck} title="Overview" />
        <KpiSkeleton count={4} />
      </div>
    );
  }

  if (errorMsg && !stats) {
    return (
      <div>
        <PageHeader icon={Truck} title="Overview" />
        <ErrorState
          title="Couldn't load dashboard"
          message={errorMsg}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (!stats) return null;

  if (isDriver) {
    return (
      <DriverOverview
        stats={stats}
        navigate={navigate}
        greeting={greetingName}
      />
    );
  }

  return (
    <PageLayoutHost
      registry={OVERVIEW_SECTIONS}
      layouts={OVERVIEW_LAYOUTS}
      sectionProps={{
        stats,
        navigate,
        has,
        hasWide,
        greetingName,
        fetchedAt: dataUpdatedAt,
        isFetching,
        refetch,
        headerConfig,
        kpiPriority,
      }}
    />
  );
}
