import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { apiJSON } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import { usePermissions } from '../../hooks/usePermissions';
import type { Vehicle, VehiclesResponse, HealthResponse, FaultsResponse, Fault, HealthData, AIDiagnoseResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';

export default function VehicleDetail() {
  const { name } = useParams<{ name: string }>();
  const { has } = usePermissions();
  const navigate = useNavigate();
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [faults, setFaults] = useState<FaultsResponse | null>(null);
  const [error, setError] = useState('');

  // ── Inline AI Diagnosis state ─────────────────────────────
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState('');
  const [diagnosisError, setDiagnosisError] = useState('');

  useEffect(() => {
    const encoded = encodeURIComponent(name!);
    apiJSON<VehiclesResponse>(`/vehicles/${encoded}`)
      .then((d) => {
        if (d.vehicles?.length) setVehicle(d.vehicles[0]);
        else setError('Vehicle not found');
      })
      .catch((e) => setError(e.message));

    if (has('can_health')) {
      apiJSON<HealthResponse>(`/vehicles/${encoded}/health`)
        .then((d) => { if (d.health) setHealth(d); })
        .catch(() => {});
    }

    if (has('can_faults')) {
      apiJSON<FaultsResponse>(`/vehicles/${encoded}/faults`)
        .then(setFaults)
        .catch(() => {});
    }
  }, [name, has]);

  if (error) return <p className="text-destructive">{error}</p>;
  if (!vehicle) return <p className="text-muted-foreground">Loading...</p>;

  const v = vehicle;
  const loc = v.location || {};
  const fuel = v.fuelPercent ?? v.fuel_percent;
  const defPct = v.defPercent ?? v.def_percent;
  const faultList: Fault[] = faults?.faults || [];
  const h: HealthData = health?.health || {};
  const healthAlerts: string[] = health?.alerts || [];

  async function diagnoseFaults() {
    if (diagnosing || !faultList.length) return;
    setDiagnosing(true);
    setDiagnosis('');
    setDiagnosisError('');
    try {
      const data = await apiJSON<AIDiagnoseResponse>('/ai/diagnose', {
        method: 'POST',
        body: {
          vehicle_name: name,
          dtcs: faultList,
          lights: health?.health ?? {},
        },
      });
      setDiagnosis(data.diagnosis);
    } catch (e) {
      setDiagnosisError(e instanceof Error ? e.message : 'Diagnosis failed');
    } finally {
      setDiagnosing(false);
    }
  }

  return (
    <div>
      <Link to="/fleet/vehicles" className="text-primary hover:underline text-sm mb-4 inline-block">
        ← Back to vehicles
      </Link>
      <h1 className="text-2xl font-bold mb-6">{v.name}</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Info card */}
        <div className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h2 className="text-lg font-semibold mb-3">Vehicle Info</h2>
          <Row label="VIN" value={v.vin} />
          <Row label="Make / Model" value={[v.make, v.model].filter(Boolean).join(' ') || '—'} />
          <Row label="Year" value={v.year} />
          <Row label="License Plate" value={v.licensePlate || v.license_plate} />
          <Row label="Company" value={v._org || v.company} />
          <Row label="Engine">
            <StatusBadge status={v.engineState || v.engine_state || 'Off'} />
          </Row>
          <Row label="Fuel" value={fuel != null ? `${Math.round(fuel)}%` : '—'} />
          {defPct != null && <Row label="DEF" value={`${Math.round(defPct)}%`} />}
        </div>

        {/* Location card */}
        <div className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h2 className="text-lg font-semibold mb-3">Location</h2>
          <Row label="Address" value={loc.reverseGeo?.formattedLocation || v.formattedAddress || v.address || '—'} />
          <Row label="Speed" value={v.speed_mph != null ? `${v.speed_mph} mph` : '—'} />
          <Row label="Latitude" value={loc.latitude ?? v.latitude} />
          <Row label="Longitude" value={loc.longitude ?? v.longitude} />
        </div>

        {/* Health card */}
        {health && (
          <div className="bg-card border border-border rounded-xl p-5 space-y-3">
            <h2 className="text-lg font-semibold mb-3">Vehicle Health</h2>
            <Row label="Battery" value={h.battery_v != null ? `${h.battery_v.toFixed(1)} V` : '—'} />
            <Row label="Oil Pressure" value={h.oil_psi != null ? `${h.oil_psi.toFixed(1)} PSI` : '—'} />
            <Row label="Coolant Temp" value={h.coolant_c != null ? `${h.coolant_c.toFixed(0)}°C` : '—'} />
            <Row label="DEF Level" value={h.def_pct != null ? `${h.def_pct.toFixed(0)}%` : '—'} />
            <Row label="Engine Load" value={h.load_pct != null ? `${h.load_pct.toFixed(0)}%` : '—'} />
            <Row label="RPM" value={h.rpm != null ? Math.round(h.rpm) : '—'} />
            <Row label="Seatbelt" value={h.seatbelt ?? '—'} />
            {healthAlerts.length > 0 && (
              <div className="mt-2">
                <p className="text-sm text-muted-foreground mb-1">Health Alerts:</p>
                {healthAlerts.map((a, i) => (
                  <span key={i} className="inline-block bg-red-500/20 text-destructive text-xs rounded px-2 py-0.5 mr-1 mb-1">
                    {a}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Faults card */}
        {faultList.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">
                Active Fault Codes ({faultList.length})
              </h2>
              {has('can_faults') && (
                <button
                  onClick={diagnoseFaults}
                  disabled={diagnosing}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-primary/15 hover:bg-primary/25 text-primary font-medium transition-colors disabled:opacity-60"
                >
                  {diagnosing ? (
                    <><span className="inline-flex gap-0.5"><span className="animate-bounce" style={{ animationDelay: '0ms' }}>•</span><span className="animate-bounce" style={{ animationDelay: '150ms' }}>•</span><span className="animate-bounce" style={{ animationDelay: '300ms' }}>•</span></span> Analyzing...</>
                  ) : diagnosis ? 'Re-diagnose' : '✨ Diagnose with AI'}
                </button>
              )}
            </div>
            <div className="space-y-2">
              {(faultList as unknown as Record<string, unknown>[]).map((f, i) => {
                const j = (f.j1939 as Record<string, unknown> | undefined) ?? {};
                const spn   = (j.spnDescription   ?? f.spnDescription   ?? f.code ?? 'DTC') as string;
                const fmi   = (j.fmiDescription   ?? f.fmiDescription)  as string | undefined;
                const src   = (j.sourceAddressName ?? f.sourceAddressName) as string | undefined;
                const count = (f.occurrences ?? f.occurrenceCount)       as number | undefined;
                const desc  = f.description as string | undefined;
                return (
                <div key={i} className="bg-muted rounded-lg p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-orange-600 dark:text-orange-400">{spn}</span>
                    {fmi && (
                      <span className="text-xs text-muted-foreground">FMI: {fmi}</span>
                    )}
                  </div>
                  {desc && <p className="text-muted-foreground mt-1">{desc}</p>}
                  {(count != null || src) && (
                    <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                      {count != null && <span>× {count} occurrence{count !== 1 ? 's' : ''}</span>}
                      {src && <span>Source: {src}</span>}
                    </div>
                  )}
                </div>
                );
              })}
            </div>

            {/* Inline AI Diagnosis result */}
            {diagnosisError && (
              <div className="mt-4 text-destructive text-sm bg-destructive/10 rounded-lg px-3 py-2">{diagnosisError}</div>
            )}
            {diagnosis && (
              <div className="mt-4 bg-primary/5 border border-primary/20 rounded-xl p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold text-primary">✨ AI Diagnosis</span>
                  <button
                    onClick={() => navigate('/ai/chat', { state: { initialMessage: `Tell me more about the fault codes on Truck ${name}` } })}
                    className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors"
                  >
                    Open in AI Chat
                  </button>
                </div>
                <div
                  className="text-sm text-foreground/90 leading-relaxed ai-response"
                  dangerouslySetInnerHTML={{ __html: formatAIResponse(diagnosis) }}
                />
              </div>
            )}
          </div>
        )}

        {faultList.length === 0 && faults && (
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-lg font-semibold mb-3">Fault Codes</h2>
            <p className="text-green-600 dark:text-green-400 text-sm">No active fault codes</p>
          </div>
        )}
      </div>

      {/* Phase E25 — 7-day hourly telemetry timeline */}
      <TimelineCard vehicleName={name!} />
    </div>
  );
}

// ── Timeline (Phase E25) ───────────────────────────────────

interface TimelinePoint {
  hour_utc: string;
  miles?: number | null;
  drive_min?: number | null;
  idle_min?: number | null;
  max_speed_mph?: number | null;
  harsh_event_count?: number | null;
}

interface TimelineResponse {
  name?: string;
  vehicle_id?: string;
  days?: number;
  points: TimelinePoint[];
  error?: string;
}

function TimelineCard({ vehicleName }: { vehicleName: string }) {
  // React Query keeps the timeline cached per-vehicle so flipping
  // between vehicle tabs doesn't refetch immediately.
  const { data, isLoading, error } = useQuery<TimelineResponse>({
    queryKey: ['vehicle-timeline', vehicleName],
    queryFn: () => apiJSON<TimelineResponse>(
      `/vehicles/${encodeURIComponent(vehicleName)}/timeline?days=7`,
    ),
    staleTime: 5 * 60_000,
  });

  const points = (data?.points ?? []).map((p) => ({
    ...p,
    label: p.hour_utc?.slice(5, 13).replace('T', ' '),
  }));

  return (
    <div className="mt-6 bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">7-Day Activity</h2>
        <span className="text-xs text-muted-foreground">hourly roll-up</span>
      </div>
      {isLoading && <p className="text-sm text-muted-foreground">Loading timeline…</p>}
      {error && <p className="text-sm text-destructive">Failed to load timeline.</p>}
      {!isLoading && !error && points.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No telemetry data yet — the warehouse roll-up runs hourly.
        </p>
      )}
      {points.length > 0 && (
        <div style={{ width: '100%', height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  background: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: 8,
                  fontSize: 12,
                }}
              />
              <Line type="monotone" dataKey="miles" stroke="#0a84ff" strokeWidth={2} dot={false} name="Miles" />
              <Line type="monotone" dataKey="max_speed_mph" stroke="#34c759" strokeWidth={2} dot={false} name="Max mph" />
              <Line type="monotone" dataKey="harsh_event_count" stroke="#ff453a" strokeWidth={2} dot={false} name="Harsh" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

interface RowProps {
  label: string;
  value?: string | number | null;
  children?: React.ReactNode;
}

function Row({ label, value, children }: RowProps) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children || <span>{value ?? '—'}</span>}
    </div>
  );
}
