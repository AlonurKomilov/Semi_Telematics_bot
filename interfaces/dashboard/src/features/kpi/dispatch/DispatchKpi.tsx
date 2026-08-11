/**
 * Dispatch KPI — the dispatch section's grades page.
 *
 * One SECTION of the per-role KPI family (moved here from the old
 * single /kpi page when the sections split into their own homes).
 * ``can_kpi`` gates the read; the header's section switcher walks an
 * owner/admin across sections, while each role's view lands here (or
 * on its own section) via /kpi.
 *
 * Grades are shared analytics; payout amounts live behind the
 * Incentives button (``can_kpi_incentives``).
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useRoleView } from '../../../context/RoleViewContext';
import { Button } from '../../../components/ui/button';
import { BadgeDollarSign, ChevronDown, Gauge } from 'lucide-react';
import DataGrid from '../../../components/datagrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton, DateRangePresets,
} from '../../../components/shell';
import { ActionMenu } from '../../../components/ui/context-menu';
import { toneClasses, type Tone } from '../../../lib/status';
import type { AnyColumn } from '../../../types';
import { FeatureConfigGear } from '../../_lib/FeatureConfigGear';
import { ConfigMovedNotice } from '../../_lib/ConfigMovedNotice';
import { getDispatcherKpis } from '../api';
import type { DispatcherKpisResponse } from '../api';
import { KPI_SECTIONS } from '../sections';

const GRADE_TONE: Record<string, Tone> = {
  A: 'ok', B: 'info', C: 'warn', D: 'danger',
};

function GradePill({ value }: { value: unknown }) {
  const v = String(value || 'B');
  return (
    <span className={`inline-flex items-center justify-center w-7 h-6 rounded-full border text-xs font-semibold ${toneClasses(GRADE_TONE[v] ?? 'neutral')}`}>
      {v}
    </span>
  );
}

function Money({ value }: { value: unknown }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const n = Number(value);
  return (
    <span className={`tabular-nums font-medium ${n < 0 ? 'text-danger' : ''}`}>
      ${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}
    </span>
  );
}

function Num({ value, suffix = '' }: { value: unknown; suffix?: string }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  return <span className="tabular-nums">{String(value)}{suffix}</span>;
}

const COLUMNS: AnyColumn[] = [
  { key: 'dispatcher_name', label: 'Dispatcher', sortable: true, filterable: true },
  { key: 'grade', label: 'Grade', sortable: true, render: (v) => <GradePill value={v} /> },
  { key: 'revenue', label: 'Revenue', sortable: true, render: (v) => <Money value={v} /> },
  { key: 'rpm', label: 'RPM', sortable: true, render: (v) => <Num value={v} /> },
  { key: 'empty_pct', label: 'Empty %', sortable: true, render: (v) => <Num value={v} suffix="%" /> },
  { key: 'gross', label: 'Gross', sortable: true, render: (v) => <Money value={v} /> },
  { key: 'loads', label: 'Loads', sortable: true, render: (v) => <Num value={v} /> },
  { key: 'trucks', label: 'Trucks', sortable: true, render: (v) => <Num value={v} /> },
  { key: 'gross_per_truck', label: 'Gross/truck', sortable: true, render: (v) => <Money value={v} /> },
];

const WINDOWS = [7, 30, 90];

/** The section switcher — one control, every section page renders it in
 *  the same header slot.  Not-yet-built sections stay visible but
 *  disabled: the map of what KPI will grade is part of the product. */
function SectionSwitcher({ current }: { current: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <ActionMenu
      items={KPI_SECTIONS.map((s) => ({
        key: s.key,
        label: s.ready
          ? s.label
          : `${s.label} — ${t('kpi_page.coming', 'not built yet')}`,
        disabled: !s.ready || s.key === current,
        onSelect: () => navigate(s.path),
      }))}
    >
      <Button variant="outline" size="sm">
        {KPI_SECTIONS.find((s) => s.key === current)?.label ?? current}
        <ChevronDown size={14} className="ml-1.5 text-muted-foreground" />
      </Button>
    </ActionMenu>
  );
}

export default function DispatchKpi() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { viewHas } = useRoleView();
  const [days, setDays] = useState(30);

  const { data, isLoading, isFetching, error } = useQuery<DispatcherKpisResponse>({
    queryKey: ['kpi-dispatchers', days],
    queryFn: () => getDispatcherKpis(days),
  });

  const rows = data?.dispatchers ?? [];

  return (
    <div>
      <PageHeader
        icon={Gauge}
        title={t('kpi_dispatch.title', 'Dispatch KPI')}
        description={t(
          'kpi_dispatch.description',
          'Dispatcher performance across the account — graded against the dispatch thresholds.',
        )}
        actions={(
          <div className="flex items-center gap-2">
            <SectionSwitcher current="dispatch" />
            {/* Compensation surface, its own permission — the button only
                renders for holders, and the route re-checks. */}
            {viewHas('can_kpi_incentives') && (
              <Button variant="outline" size="sm" onClick={() => navigate('/kpi/incentives')}>
                <BadgeDollarSign size={16} className="mr-1.5" />
                {t('kpi_page.incentives', 'Incentives')}
              </Button>
            )}
            <FeatureConfigGear
              feature={t('nav.kpi', 'KPI')}
              to="/kpi/configuration"
            />
          </div>
        )}
      />

      <ConfigMovedNotice what="Grading thresholds" />

      <div className="mb-3 flex justify-end">
        {/* Window picker — the SSOT selector in chip form. */}
        <DateRangePresets
          variant="segments"
          value={days}
          onChange={setDays}
          options={WINDOWS.map((w) => ({ label: `${w}d`, days: w }))}
          isFetching={isFetching}
        />
      </div>

      {isLoading && <TableSkeleton />}
      {!isLoading && error != null && (
        <ErrorState message={error instanceof Error ? error.message : String(error)} />
      )}
      {!isLoading && error == null && rows.length === 0 && (
        <EmptyState
          icon={Gauge}
          title={t('kpi_page.empty_title', 'No load data in this window')}
          description={t(
            'kpi_page.empty_desc',
            'KPIs are computed from your Loads — add loads or sync your TMS, then check back.',
          )}
        />
      )}
      {!isLoading && error == null && rows.length > 0 && (
        <DataGrid
          tableId="kpi-dispatchers"
          columns={COLUMNS}
          data={rows as unknown as Record<string, unknown>[]}
          searchKey="dispatcher_name"
          searchPlaceholder={t('kpi_page.search', 'Search dispatcher…')}
        />
      )}

    </div>
  );
}
