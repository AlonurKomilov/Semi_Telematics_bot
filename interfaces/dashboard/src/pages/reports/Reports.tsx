import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { FileText, Download, Sparkles } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  DateRangePresets,
} from '../../components/shell';
import type {
  FaultVehicle, FaultReportResponse,
  FuelVehicle, FuelReportResponse,
  HealthVehicle, HealthReportResponse,
  EfficiencyVehicle, EfficiencyReportResponse,
  AnyColumn,
} from '../../types';

type TabKey = 'faults' | 'fuel' | 'health' | 'efficiency';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'faults', label: 'Faults' },
  { key: 'fuel', label: 'Fuel & DEF' },
  { key: 'health', label: 'Health' },
  { key: 'efficiency', label: 'Efficiency' },
];

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    critical: 'bg-red-500/15 text-red-600 dark:text-red-400',
    warning: 'bg-orange-500/15 text-orange-600 dark:text-orange-400',
    caution: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400',
    ok: 'bg-green-500/15 text-green-600 dark:text-green-400',
  };
  const cls = colors[severity] || 'bg-gray-500/20 text-muted-foreground';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}`}>{severity}</span>;
}

function FuelBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-muted-foreground">N/A</span>;
  const cls = pct <= 15 ? 'text-destructive' : pct <= 30 ? 'text-yellow-700 dark:text-yellow-400' : 'text-green-600 dark:text-green-400';
  return <span className={cls}>{pct}%</span>;
}

const faultCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'dtc_count', label: 'DTCs', sortable: true },
  { key: 'severity', label: 'Severity', render: (v) => <SeverityBadge severity={v as string} /> },
  { key: 'fault_time', label: 'Last Seen', render: (v) => v ? new Date(v as string).toLocaleString() : '—' },
];

const fuelCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'fuel_pct', label: 'Fuel', sortable: true, render: (v) => <FuelBadge pct={v as number | null} /> },
  { key: 'def_pct', label: 'DEF', sortable: true, render: (v) => <FuelBadge pct={v as number | null} /> },
  { key: 'fuel_time', label: 'Updated', render: (v) => v ? new Date(v as string).toLocaleString() : '—' },
];

const healthCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'battery_v', label: 'Battery (V)', sortable: true, render: (v) => v != null ? `${v}V` : '—' },
  { key: 'oil_psi', label: 'Oil (psi)', sortable: true, render: (v) => v != null ? `${v}` : '—' },
  { key: 'coolant_c', label: 'Coolant (°C)', sortable: true, render: (v) => v != null ? `${v}°C` : '—' },
  { key: 'def_pct', label: 'DEF %', sortable: true, render: (v) => <FuelBadge pct={v as number | null} /> },
  {
    key: 'alerts', label: 'Alerts',
    render: (v) => {
      const a = v as string[];
      return a && a.length > 0
        ? <span className="text-destructive text-xs">{a.join(', ')}</span>
        : <span className="text-green-600 dark:text-green-400 text-xs">OK</span>;
    },
  },
];

const effCols: AnyColumn[] = [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'driver_name', label: 'Driver', sortable: true },
  { key: 'miles', label: 'Miles', sortable: true },
  { key: 'mpg', label: 'MPG', sortable: true },
  { key: 'drive_hours', label: 'Drive (h)', sortable: true },
  { key: 'idle_hours', label: 'Idle (h)', sortable: true },
  { key: 'eco_pct', label: 'Eco %', sortable: true, render: (v) => <StatusBadge status={`${v}%`} /> },
  { key: 'overspeed_min', label: 'Overspeed (min)', sortable: true },
];

const COLUMNS_MAP: Record<TabKey, AnyColumn[]> = {
  faults: faultCols,
  fuel: fuelCols,
  health: healthCols,
  efficiency: effCols,
};

export default function Reports() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabKey>('faults');
  const navigate = useNavigate();
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [summary, setSummary] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [days, setDays] = useState(30);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError('');
    const params = tab === 'efficiency' ? `?days=${days}` : '';
    apiJSON<FaultReportResponse | FuelReportResponse | HealthReportResponse | EfficiencyReportResponse>(
      `/reports/${tab}${params}`
    )
      .then((d) => {
        const items = 'vehicles' in d ? d.vehicles : [];
        setData(items as unknown as Record<string, unknown>[]);
        // Build summary string
        if (tab === 'faults') {
          const f = d as FaultReportResponse;
          setSummary(`${f.faulted_count} faulted of ${f.total_vehicles} vehicles`);
        } else if (tab === 'fuel') {
          const f = d as FuelReportResponse;
          const s = f.summary;
          setSummary(
            `Avg fuel: ${s.avg_fuel_pct ?? '—'}% · Critical: ${s.critical} · Low: ${s.low} · Good: ${s.good}`
          );
        } else if (tab === 'health') {
          const h = d as HealthReportResponse;
          setSummary(`${h.count} vehicles · ${h.alert_count} active alerts`);
        } else {
          const e = d as EfficiencyReportResponse;
          setSummary(`${e.count} vehicles · ${e.days}-day period`);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [tab, days]);

  async function downloadReport(fmt: 'pdf' | 'csv') {
    setExporting(true);
    try {
      const params = new URLSearchParams({ report_type: tab, fmt });
      if (tab === 'efficiency') params.set('days', String(days));
      const res = await apiFetch(`/reports/export?${params}`);
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${tab}_report.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <PageHeader
        icon={FileText}
        title={t('pages.reports_title')}
        description={t('pages.reports_desc')}
        actions={
          <div className="flex items-center gap-2">
            {tab === 'efficiency' && (
              <DateRangePresets value={days} onChange={setDays} />
            )}
            <button
              onClick={() => downloadReport('pdf')}
              disabled={exporting}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-background border border-border rounded-md text-xs font-medium hover:bg-muted disabled:opacity-50 transition"
            >
              <Download size={12} />
              PDF
            </button>
            <button
              onClick={() => downloadReport('csv')}
              disabled={exporting}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-background border border-border rounded-md text-xs font-medium hover:bg-muted disabled:opacity-50 transition"
            >
              <Download size={12} />
              CSV
            </button>
          </div>
        }
      />

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
              tab === t.key ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {summary && (
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm text-muted-foreground">{summary}</p>
          {tab === 'faults' && (
            <button
              onClick={() => navigate('/ai/chat', { state: { initialMessage: 'Analyze the active vehicle fault codes and tell me which trucks need attention' } })}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-primary/15 hover:bg-primary/25 text-primary font-medium transition shrink-0 ml-3"
            >
              <Sparkles size={12} />
              Ask AI about faults
            </button>
          )}
        </div>
      )}
      {error && <div className="mb-3"><ErrorState message={error} /></div>}

      {loading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : data.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No data for this report"
          description="Try a different tab or widen the date range — reports populate as soon as the underlying telematics data arrives."
        />
      ) : (
        <DataTable
          columns={COLUMNS_MAP[tab]}
          data={data}
          searchKey="vehicle_name"
        />
      )}
    </div>
  );
}
