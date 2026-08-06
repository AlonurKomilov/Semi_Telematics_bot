/**
 * KPI — the account-wide performance analytics surface.
 *
 * One shared page (``can_kpi``, delegatable to any role).  Sections are
 * DOMAINS: Dispatchers first (computed live from the canonical loads);
 * Fleet / Safety / Drivers compose their features' aggregates in later
 * increments — each future section is one service function + one tab.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Gauge } from 'lucide-react';
import DataGrid from '../../components/datagrid';
import {
  PageHeader, EmptyState, ErrorState, TableSkeleton, DateRangePresets,
} from '../../components/shell';
import { Tip } from '../../components/tooltip';
import { toneClasses, type Tone } from '../../lib/status';
import type { AnyColumn } from '../../types';
import KpiConfigPanel from './config/KpiConfigPanel';
import { FeatureConfigGear } from '../_lib/FeatureConfigGear';
import { getDispatcherKpis } from './api';
import type { DispatcherKpisResponse } from './api';
import { ConfigMovedNotice } from '../_lib/ConfigMovedNotice';

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

// Domain sections — Dispatchers is live; the rest arrive as their service
// functions land (each is one tab + one loader, no page rework).
const SECTIONS = [
  { key: 'dispatchers', label: 'Dispatchers', ready: true },
  { key: 'fleet', label: 'Fleet', ready: false },
  { key: 'safety', label: 'Safety', ready: false },
  { key: 'drivers', label: 'Drivers', ready: false },
];

export default function Kpi() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const [section, setSection] = useState('dispatchers');

  const { data, isLoading, isFetching, error } = useQuery<DispatcherKpisResponse>({
    queryKey: ['kpi-dispatchers', days],
    queryFn: () => getDispatcherKpis(days),
    enabled: section === 'dispatchers',
  });

  const rows = data?.dispatchers ?? [];

  return (
    <div>
      <PageHeader
        icon={Gauge}
        title={t('nav.kpi', 'KPI')}
        description={t(
          'kpi_page.description',
          'Performance across the account — graded against your thresholds.',
        )}
        /* The gear is the ONE config entry point, in the same header slot
           on every feature.  It replaced a bespoke "Thresholds" button
           with a sliders icon — which read as a filter, and taught the
           user nothing transferable about where the next feature keeps
           its settings.  The gear self-gates on can_manage_config_all, so
           no permission check is needed here. */
        actions={(
          <FeatureConfigGear feature={t('nav.kpi', 'KPI')}>
            <KpiConfigPanel
              onSaved={() => qc.invalidateQueries({ queryKey: ['kpi-dispatchers'] })}
            />
          </FeatureConfigGear>
        )}
      />

      <ConfigMovedNotice what="Grading thresholds" />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        {/* Domain sections */}
        <div className="flex items-center gap-1.5">
          {SECTIONS.map((s) => (
            // Span wrapper: a disabled button swallows pointer events,
            // so the "Coming soon" Tip anchors on the span around it.
            <Tip key={s.key} label={s.ready ? undefined : 'Coming soon'}>
              <span className="inline-flex">
                <button
                  type="button"
                  disabled={!s.ready}
                  onClick={() => s.ready && setSection(s.key)}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    section === s.key
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'bg-card text-foreground border-border hover:border-ring disabled:opacity-40'
                  }`}
                >
                  {s.label}
                </button>
              </span>
            </Tip>
          ))}
        </div>
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
          // Enables the 3-dot column menu — which is also the only
          // entry point to the filter popover, so the ``filterable``
          // column config below was unreachable without this.
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
