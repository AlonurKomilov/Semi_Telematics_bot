/**
 * Vehicle Health card — battery / oil / coolant / DEF / engine load /
 * RPM / seatbelt / cabin weather + health alerts pulled from the
 * fleet-wide weather feed.
 *
 * Layouts list this for Fleet primary and Safety as incident context.
 * Permission gate (``can_health``) is independent — a user without
 * that permission renders nothing here even when the layout includes
 * the section.
 */
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import { CardSkeleton } from '../../../components/shell';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import type { HealthData, HealthResponse } from '../../../types';
import { toneClasses } from '../../../lib/status';
import { Row } from './_shared/Row';
import type { VehicleSectionProps } from './_shared/types';

interface CabinWeather {
  temp_f: number | null;
  temp_c: number | null;
  baro_inhg: number | null;
  temp_time: string | null;
}

interface FleetWeatherEntry {
  name: string;
  temp_f: number | null;
  temp_c: number | null;
  baro_inhg: number | null;
  temp_time: string | null;
}

interface FleetWeatherResponse {
  vehicles: FleetWeatherEntry[];
}

export default function VehicleHealth({ vehicleName, company }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  const hasHealthPerm = has('can_health');

  const { data: health, isLoading: healthLoading } = useQuery<HealthResponse | null>({
    queryKey: ['vehicle-health', vehicleName, company ?? ''],
    queryFn: () => {
      const qs = company ? `?company=${encodeURIComponent(company)}` : '';
      return apiJSON<HealthResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/health${qs}`,
      );
    },
    enabled: hasHealthPerm,
    staleTime: 60_000,
  });

  // Cabin weather lives in the fleet-wide /vehicles/weather feed.  No
  // per-vehicle endpoint, so we filter the response by name.  Same
  // permission gate as health (it surfaces alongside).
  const { data: weather } = useQuery<CabinWeather | null>({
    queryKey: ['vehicle-cabin-weather', vehicleName],
    queryFn: async () => {
      const d = await apiJSON<FleetWeatherResponse>('/vehicles/weather');
      const entry = (d.vehicles || []).find((w) => w.name === vehicleName);
      return entry
        ? {
            temp_f: entry.temp_f,
            temp_c: entry.temp_c,
            baro_inhg: entry.baro_inhg,
            temp_time: entry.temp_time,
          }
        : null;
    },
    enabled: hasHealthPerm,
    staleTime: 60_000,
  });

  if (!hasHealthPerm) return null;
  if (healthLoading) return <CardSkeleton height="h-64" />;
  if (!health?.health) return null;

  const h: HealthData = health.health;
  const healthAlerts: string[] = health.alerts || [];

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-3">
      <h2 className="text-lg font-semibold mb-3">Vehicle Health</h2>
      <Row label="Battery" value={h.battery_v != null ? `${h.battery_v.toFixed(1)} V` : '—'} />
      <Row label="Oil Pressure" value={h.oil_psi != null ? `${h.oil_psi.toFixed(1)} PSI` : '—'} />
      <Row label="Coolant Temp" value={h.coolant_c != null ? `${h.coolant_c.toFixed(0)}°C` : '—'} />
      <Row label="DEF Level" value={h.def_pct != null ? `${h.def_pct.toFixed(0)}%` : '—'} />
      <Row label="Engine Load" value={h.load_pct != null ? `${h.load_pct.toFixed(0)}%` : '—'} />
      <Row label="RPM" value={h.rpm != null ? Math.round(h.rpm) : '—'} />
      <Row label="Seatbelt" value={h.seatbelt ?? '—'} />
      {weather && (weather.temp_f != null || weather.baro_inhg != null) && (
        <>
          <Row
            label="Cabin Temp"
            value={weather.temp_f != null ? `${weather.temp_f}°F` : '—'}
          />
          <Row
            label="Barometer"
            value={weather.baro_inhg != null ? `${weather.baro_inhg} inHg` : '—'}
          />
        </>
      )}
      {healthAlerts.length > 0 && (
        <div className="mt-2">
          <p className="text-sm text-muted-foreground mb-1">Health Alerts:</p>
          {healthAlerts.map((a, i) => (
            <span
              key={i}
              className={`inline-block text-xs rounded px-2 py-0.5 mr-1 mb-1 ${toneClasses('danger')}`}
            >
              {a}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
