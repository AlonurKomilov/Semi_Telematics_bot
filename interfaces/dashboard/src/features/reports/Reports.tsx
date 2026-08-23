import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import { Download, Sparkles, FileText } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import DataGrid from '../../components/datagrid';
import StatusBadge from '../../components/StatusBadge';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  DateRangePresets,
} from '../../components/shell';
import type { ReportsLayoutOutletContext } from './ReportsLayout';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import type {
  FaultReportResponse,
  FuelReportResponse,
  HealthReportResponse,
  EfficiencyReportResponse,
  AnyColumn,
} from '../../types';
import { REPORTS } from '../../data/reports';
import { statusTone, toneClasses } from '../../lib/status';

// Tabs on this page = the API-exportable reports (excludes Camera
// Check which is bot-delivery-only).  Sourced from the canonical
// report registry so this list and the bot scheduler can't drift.
type TabKey = 'faults' | 'fuel' | 'health' | 'efficiency';

const ALL_TABS: { key: TabKey; label: string }[] = REPORTS
  .filter((r) => r.hasApiExport)
  .map((r) => ({ key: r.key as TabKey, label: r.labelShort }));

// Per-persona tab visibility.  Mirrors the strict-binding rule:
// each persona's tabs match their workspace.  Owner/Admin see all
// (executive view); Fleet sees mechanical (faults + health + fuel &
// DEF + efficiency); Safety sees only efficiency (driver behaviour
// contributes to scorecards); Dispatcher doesn't reach this page
// (no can_faults).  HR / Accounting don't reach this page either.
//
// To see another persona's tabs the operator switches view via the
// persona selector — different subdomain, different active view,
// different filtered tab list.
const TABS_FOR_VIEW: Record<string, ReadonlyArray<TabKey>> = {
  owner:      ['faults', 'fuel', 'health', 'efficiency'],
  admin:      ['faults', 'fuel', 'health', 'efficiency'],
  fleet:      ['faults', 'fuel', 'health', 'efficiency'],
  safety:     ['efficiency'],
  dispatcher: ['fuel'],  // dispatcher doesn't have can_faults today but defensive
  hr:         [],
  accounting: ['fuel', 'efficiency'],
  driver:     [],
};

function SeverityBadge({ severity }: { severity: string }) {
  // ``caution`` isn't in the shared status map (it's a fault-report
  // specific step between ok and warning); treat it as attention/warn.
  const tone = severity === 'caution' ? 'warn' : statusTone(severity);
  return <span className={`px-2 py-0.5 rounded-md text-xs font-medium capitalize ${toneClasses(tone)}`}>{severity}</span>;
}

function FuelBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-muted-foreground">N/A</span>;
  const cls = pct <= 15 ? 'text-danger' : pct <= 30 ? 'text-warn' : 'text-ok';
  return <span className={cls}>{pct}%</span>;
}

const faultCols = (tz: string): AnyColumn[] => [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'dtc_count', label: 'DTCs', sortable: true },
  { key: 'severity', label: 'Severity', render: (v) => <SeverityBadge severity={v as string} /> },
  { key: 'fault_time', label: 'Last Seen', render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—' },
];

const fuelCols = (tz: string): AnyColumn[] => [
  { key: 'vehicle_name', label: 'Vehicle', sortable: true },
  { key: 'company', label: 'Company', sortable: true },
  { key: 'fuel_pct', label: 'Fuel', sortable: true, render: (v) => <FuelBadge pct={v as number | null} /> },
  { key: 'def_pct', label: 'DEF', sortable: true, render: (v) => <FuelBadge pct={v as number | null} /> },
  { key: 'fuel_time', label: 'Updated', render: (v) => v ? formatDate(v as string, { timeZone: tz }) : '—' },
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
        ? <span className="text-danger text-xs">{a.join(', ')}</span>
        : <span className="text-ok text-xs">OK</span>;
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
  { key: 'eco_pct', label: 'Eco %', sortable: true, render: (v) => (
    v == null
      ? <span className="text-muted-foreground">—</span>
      : <StatusBadge status={`${v}%`} />
  ) },
  { key: 'overspeed_min', label: 'Overspeed (min)', sortable: true },
];

const columnsMap = (tz: string): Record<TabKey, AnyColumn[]> => ({
  faults: faultCols(tz),
  fuel: fuelCols(tz),
  health: healthCols,
  efficiency: effCols,
});

export default function Reports() {
  const tz = useTimezone();
  const { persona } = useShellConfig();
  // Outlet context comes from ReportsLayout — lets this child page
  // push the PDF/CSV export buttons (and the efficiency-only Date
  // Range presets) into the layout's shared action slot.
  const outletCtx = useOutletContext<ReportsLayoutOutletContext | undefined>();
  // Filter the universal tab list down to this persona's allowed
  // tabs (strict role binding).  Fall back to the owner superset if
  // the persona isn't in the map.
  const visibleTabs = useMemo(() => {
    const allowed = new Set<TabKey>(
      TABS_FOR_VIEW[persona] ?? TABS_FOR_VIEW.owner ?? [],
    );
    return ALL_TABS.filter((t) => allowed.has(t.key));
  }, [persona]);
  // Initial tab comes from ``?tab=`` when present (used by Telegram
  // bot deep links — e.g. ``cmd_fuel`` redirects to
  // ``/reports?tab=fuel`` so the user lands on the right tab).  When
  // the query param is missing or not visible to this persona, fall
  // back to the persona's first allowed tab — for Safety that's
  // ``efficiency``, for everyone else ``faults``.  Sticky after the
  // first render so a user navigating tabs doesn't get reset on each
  // re-render.
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<TabKey>(() => {
    const requested = searchParams.get('tab') as TabKey | null;
    if (requested && visibleTabs.some((v) => v.key === requested)) {
      return requested;
    }
    return visibleTabs[0]?.key ?? 'faults';
  });
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

  // Push the PDF/CSV export buttons + the efficiency-only Date Range
  // presets into the layout's shared action slot.  Re-pushes whenever
  // the visible action chrome changes (tab swap → DateRangePresets
  // appears/disappears; exporting flag toggles button disabled).
  useEffect(() => {
    if (!outletCtx) return;
    outletCtx.setActions(
      <div className="flex items-center gap-2">
        {tab === 'efficiency' && (
          <DateRangePresets value={days} onChange={setDays} />
        )}
        <button
          onClick={() => downloadReport('pdf')}
          disabled={exporting}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-background border border-border rounded-md text-xs font-medium hover:bg-muted disabled:opacity-50 transition"
        >
          <Download className="size-3" />
          PDF
        </button>
        <button
          onClick={() => downloadReport('csv')}
          disabled={exporting}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-background border border-border rounded-md text-xs font-medium hover:bg-muted disabled:opacity-50 transition"
        >
          <Download className="size-3" />
          CSV
        </button>
      </div>,
    );
    return () => outletCtx.setActions(null);
    // downloadReport is recreated each render but the layout doesn't
    // memoise its action prop — re-pushing is fine and gated by the
    // visible inputs below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, days, exporting, outletCtx]);

  return (
    <div>
      {/* Tabs — filtered to the active persona's relevant report types. */}
      <div className="flex gap-1 mb-4">
        {visibleTabs.map((t) => (
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
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-primary/15 hover:bg-primary/25 text-primary font-medium transition shrink-0 ml-3 min-h-tap"
            >
              <Sparkles className="size-3" />
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
        <DataGrid
          // Per-tab tableId — each report tab (faults / fuel / health /
          // efficiency) has a different column set, so sharing one id
          // would make the persisted layout blobs fight each other.
          tableId={`reports-${tab}`}
          columns={columnsMap(tz)[tab]}
          data={data}
          searchKey="vehicle_name"
        />
      )}
    </div>
  );
}
