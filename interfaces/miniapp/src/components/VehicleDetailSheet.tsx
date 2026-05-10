/**
 * VehicleDetailSheet — full-height bottom sheet showing deep vehicle data.
 *
 * Fetches in parallel:
 *   - GET /api/vehicles/{name}/health    → battery, oil, coolant, DEF, seatbelt, engine load
 *   - GET /api/vehicles/{name}/faults    → active DTC fault codes
 *   - GET /api/vehicles/{name}/timeline  → hourly telemetry roll-up (sparkline)
 *
 * Opened by VehiclesPage when the user taps a vehicle row (multi-vehicle)
 * or the "Details" button on the single-vehicle hero.
 */

import { useEffect, useState } from 'react';
import { Placeholder } from '@telegram-apps/telegram-ui';
import { Icon24TruckOutline } from '@vkontakte/icons';
import { BottomSheet } from './BottomSheet';
import { ListRowsSkeleton } from './Skeleton';
import { apiJSON } from '../api/client';

// Actual field names returned by GET /api/vehicles/{name}/health → health sub-dict
interface HealthData {
  battery_v?: number | null;       // volts (already /1000)
  oil_psi?: number | null;         // psi
  oil_kpa?: number | null;
  load_pct?: number | null;        // 0-100 %
  seatbelt?: string | null;        // 'Buckled' | 'Unbuckled'
  coolant_c?: number | null;       // °C (already /1000)
  def_pct?: number | null;         // 0-100 %
  rpm?: number | null;
  engine_on?: boolean | null;
  [key: string]: unknown;
}

interface HealthResp {
  name: string;
  company: string;
  health: HealthData;
  alerts: string[];
}

// Samsara J1939 DTC object (raw from /api/vehicles/{name}/faults → faults[])
interface FaultCode {
  spnId?: number | null;
  fmiId?: number | null;
  spnDescription?: string | null;
  occurrenceCount?: number | null;
  txId?: number | null;
  [key: string]: unknown;
}

interface FaultsResp {
  name: string;
  faults: FaultCode[];
  fault_count: number;
}

interface TelemetryPoint {
  hour_utc: string;
  miles: number;
  drive_min: number;
  idle_min: number;
  max_speed_mph: number;
  harsh_event_count: number;
}

interface TimelineResp {
  name: string;
  days: number;
  points: TelemetryPoint[];
}

// Slim shape of the basic /api/vehicles/{name} response — only the
// odometer fields are read here; the rest of the vehicle data already
// lives in the parent VehiclesPage state.
interface BasicResp {
  vehicles?: Array<{
    name: string;
    odometer_miles: number | null;
    odometer_time: string | null;
  }>;
}

interface Props {
  /** Vehicle name to load; null = sheet closed */
  name: string | null;
  onClose: () => void;
}

/** Map backend alert IDs to concise, human-readable labels. */
const ALERT_LABELS: Record<string, string> = {
  seatbelt_unbuckled:    'Seatbelt unbuckled',
  high_coolant_temp:     'High coolant temperature',
  low_def:               'DEF level low',
  low_battery:           'Low battery voltage',
  high_engine_load:      'High engine load',
  check_engine:          'Check engine light',
  oil_pressure_low:      'Low oil pressure',
};

function formatAlert(id: string): string {
  return ALERT_LABELS[id]
    ?? id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function voltDisplay(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(2)} V`;
}

function celsiusDisplay(c: number | null | undefined): string {
  if (c == null) return '—';
  return `${c.toFixed(1)} °C`;
}

function pctDisplay(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${Math.round(v)}%`;
}

function oilDisplay(psi: number | null | undefined): string {
  if (psi == null) return '—';
  return `${psi.toFixed(1)} psi`;
}

function HealthRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      padding: '10px 0',
      borderBottom: '1px solid rgba(128,128,128,0.1)',
      gap: 12,
    }}>
      <span style={{ fontSize: 14, color: 'var(--tgui--hint_color)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: warn ? 700 : 400, color: warn ? 'var(--st-red, #ff3b30)' : 'inherit', textAlign: 'right' }}>
        {value}
      </span>
    </div>
  );
}

export function VehicleDetailSheet({ name, onClose }: Props) {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [faults, setFaults] = useState<FaultsResp | null>(null);
  const [timeline, setTimeline] = useState<TelemetryPoint[]>([]);
  // Odometer is a separate fetch against /api/vehicles/{name} so the
  // health endpoint can stay narrowly scoped.  Both come from the
  // warehouse — neither makes a Samsara call on the request path.
  const [odometerMiles, setOdometerMiles] = useState<number | null>(null);
  const [odometerTime, setOdometerTime] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!name) {
      setHealth(null);
      setFaults(null);
      setTimeline([]);
      setOdometerMiles(null);
      setOdometerTime(null);
      setError(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(false);
    const encoded = encodeURIComponent(name);
    Promise.all([
      apiJSON<HealthResp>(`/api/vehicles/${encoded}/health`),
      apiJSON<FaultsResp>(`/api/vehicles/${encoded}/faults`),
      apiJSON<TimelineResp>(`/api/vehicles/${encoded}/timeline?days=7`)
        .then(r => r.points ?? [])
        .catch(() => [] as TelemetryPoint[]), // non-critical: warehouse may be cold
      apiJSON<BasicResp>(`/api/vehicles/${encoded}`).catch(() => ({} as BasicResp)),
    ])
      .then(([h, f, pts, basic]) => {
        if (cancelled) return;
        setHealth(h);
        setFaults(f);
        setTimeline(pts);
        const match = (basic.vehicles ?? []).find(v => v.name === name);
        setOdometerMiles(match?.odometer_miles ?? null);
        setOdometerTime(match?.odometer_time ?? null);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [name]);

  return (
    <BottomSheet
      open={!!name}
      onClose={onClose}
      title={name ? (
        <span className="sheet__title-inner">
          <Icon24TruckOutline width={18} height={18} />
          {name}
        </span>
      ) : ''}
      height="full"
    >
      {loading && <ListRowsSkeleton count={6} />}

      {!loading && error && (
        <div className="centered" style={{ minHeight: 200 }}>
          <Placeholder header="Failed to load" description="Could not load vehicle details.">⚠️</Placeholder>
        </div>
      )}

      {!loading && !error && health && (
        <>
          {/* ── Health metrics ────────────────────────────────── */}
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4, marginTop: 4 }}>
            🩺 Health
          </div>
          {health.alerts && health.alerts.length > 0 && (
            <div style={{
              background: 'var(--st-red-tint, rgba(255,59,48,0.1))',
              borderRadius: 8, padding: '8px 12px', marginBottom: 8,
            }}>
              {health.alerts.map((a, i) => (
                <div key={i} style={{ fontSize: 13, color: 'var(--st-red, #ff3b30)', lineHeight: 1.5 }}>
                  ⚠ {formatAlert(a)}
                </div>
              ))}
            </div>
          )}
          {health.health && (
            <>
              {odometerMiles != null && (
                <HealthRow
                  label={
                    odometerTime
                      ? `Odometer (as of ${new Date(odometerTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`
                      : 'Odometer'
                  }
                  value={`${Math.round(odometerMiles).toLocaleString()} mi`}
                />
              )}
              <HealthRow
                label="Battery"
                value={voltDisplay(health.health.battery_v)}
                warn={(health.health.battery_v ?? Infinity) < 11.5}
              />
              <HealthRow
                label="Engine Load"
                value={pctDisplay(health.health.load_pct)}
                warn={(health.health.load_pct ?? 0) > 90}
              />
              <HealthRow
                label="Oil Pressure"
                value={oilDisplay(health.health.oil_psi)}
              />
              <HealthRow
                label="Coolant Temp"
                value={celsiusDisplay(health.health.coolant_c)}
                warn={(health.health.coolant_c ?? 0) > 105}
              />
              <HealthRow
                label="DEF Level"
                value={pctDisplay(health.health.def_pct)}
                warn={(health.health.def_pct ?? 100) < 10}
              />
              <HealthRow
                label="Seatbelt"
                value={health.health.seatbelt ?? '—'}
                warn={health.health.seatbelt === 'Unbuckled'}
              />
            </>
          )}

          {/* ── Active fault codes ─────────────────────────────── */}
          {faults && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
                ⚠️ Faults ({faults.fault_count})
              </div>
              {faults.fault_count === 0 ? (
                <div style={{ fontSize: 14, color: 'var(--tgui--hint_color)', padding: '12px 0' }}>
                  ✅ No active fault codes
                </div>
              ) : (
                faults.faults.map((f, i) => {
                  // Build a readable code label: "SPN 1234 / FMI 5"
                  const codeLabel = [
                    f.spnId != null ? `SPN ${f.spnId}` : null,
                    f.fmiId != null ? `FMI ${f.fmiId}` : null,
                  ].filter(Boolean).join(' / ') || 'DTC';
                  return (
                    <div key={i} style={{
                      padding: '10px 0',
                      borderBottom: '1px solid rgba(128,128,128,0.1)',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                        <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--st-red, #ff3b30)', fontFamily: 'monospace' }}>
                          {codeLabel}
                        </div>
                        {f.occurrenceCount != null && f.occurrenceCount > 1 && (
                          <div style={{ fontSize: 11, color: 'var(--tgui--hint_color)', flexShrink: 0 }}>
                            ×{f.occurrenceCount}
                          </div>
                        )}
                      </div>
                      {f.spnDescription && (
                        <div style={{ fontSize: 13, color: 'var(--tgui--text_color)', marginTop: 3, lineHeight: 1.4 }}>
                          {f.spnDescription}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}

          {/* ── 7-day telemetry sparkline ─────────────────────── */}
          {timeline.length > 0 && (
            <TelemetrySparkline points={timeline} />
          )}
        </>
      )}
    </BottomSheet>
  );
}

// ── Telemetry sparkline ──────────────────────────────────────────────────

interface SparklineProps {
  points: TelemetryPoint[];
}

/** Pure-SVG bar chart: daily miles driven over the last 7 days. */
function TelemetrySparkline({ points }: SparklineProps) {
  // Aggregate hourly points into calendar days (UTC).
  const byDay = new Map<string, { miles: number; harsh: number }>();
  for (const p of points) {
    const day = p.hour_utc.slice(0, 10); // YYYY-MM-DD
    const existing = byDay.get(day) ?? { miles: 0, harsh: 0 };
    byDay.set(day, {
      miles: existing.miles + (p.miles ?? 0),
      harsh: existing.harsh + (p.harsh_event_count ?? 0),
    });
  }

  const days = Array.from(byDay.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-7);

  if (days.length === 0) return null;

  const maxMiles = Math.max(...days.map(d => d[1].miles), 1);
  const chartH = 60;
  const barW = 28;
  const gap = 6;
  const totalW = days.length * (barW + gap) - gap;

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>📈 Last 7 days</div>
      <svg
        width={totalW}
        height={chartH + 24}
        style={{ display: 'block', width: '100%', height: 'auto', overflow: 'visible' }}
        viewBox={`0 0 ${totalW} ${chartH + 24}`}
      >
        {days.map(([day, { miles, harsh }], i) => {
          const barH = Math.max(Math.round((miles / maxMiles) * chartH), 2);
          const x = i * (barW + gap);
          const y = chartH - barH;
          const label = new Date(day + 'T00:00:00Z')
            .toLocaleDateString(undefined, { weekday: 'short' });
          return (
            <g key={day}>
              <rect
                x={x}
                y={y}
                width={barW}
                height={barH}
                rx={4}
                fill={harsh > 0 ? 'var(--st-orange, #ff9f0a)' : 'var(--st-blue, #0a84ff)'}
                opacity={0.85}
              />
              {harsh > 0 && (
                <text x={x + barW / 2} y={y - 4} textAnchor="middle" fontSize={9}
                  fill="var(--st-orange, #ff9f0a)">{harsh}⚡</text>
              )}
              <text x={x + barW / 2} y={chartH + 14} textAnchor="middle" fontSize={10}
                fill="var(--tgui--hint_color)">{label}</text>
            </g>
          );
        })}
      </svg>
      <div style={{ fontSize: 11, color: 'var(--tgui--hint_color)', marginTop: 4 }}>
        Miles driven per day · orange = harsh events · max {Math.round(maxMiles)} mi
      </div>
    </div>
  );
}
