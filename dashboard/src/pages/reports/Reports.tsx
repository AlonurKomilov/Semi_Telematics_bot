import { useEffect, useState } from 'react';
import { apiJSON, apiFetch } from '../../api/client';
import DataTable from '../../components/DataTable';
import StatusBadge from '../../components/StatusBadge';
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
    critical: 'bg-red-500/20 text-red-400',
    warning: 'bg-orange-500/20 text-orange-400',
    caution: 'bg-yellow-500/20 text-yellow-400',
    ok: 'bg-green-500/20 text-green-400',
  };
  const cls = colors[severity] || 'bg-gray-500/20 text-gray-400';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}`}>{severity}</span>;
}

function FuelBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-gray-500">N/A</span>;
  const cls = pct <= 15 ? 'text-red-400' : pct <= 30 ? 'text-yellow-400' : 'text-green-400';
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
        ? <span className="text-red-400 text-xs">{a.join(', ')}</span>
        : <span className="text-green-400 text-xs">OK</span>;
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
  const [tab, setTab] = useState<TabKey>('faults');
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [summary, setSummary] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [days, setDays] = useState(7);
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
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Reports</h1>
        <div className="flex items-center gap-2">
          {tab === 'efficiency' && (
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-300"
            >
              {[7, 14, 30].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          )}
          <button
            onClick={() => downloadReport('pdf')}
            disabled={exporting}
            className="px-3 py-1.5 bg-red-600/80 hover:bg-red-600 disabled:opacity-50 rounded text-sm font-medium transition"
          >
            PDF
          </button>
          <button
            onClick={() => downloadReport('csv')}
            disabled={exporting}
            className="px-3 py-1.5 bg-green-600/80 hover:bg-green-600 disabled:opacity-50 rounded text-sm font-medium transition"
          >
            CSV
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
              tab === t.key ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {summary && <p className="text-sm text-gray-400 mb-3">{summary}</p>}
      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-gray-500">Loading...</p>
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
