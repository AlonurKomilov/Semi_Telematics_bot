/**
 * Vehicle Health card — battery / oil / coolant / DEF / engine load /
 * RPM / seatbelt / cabin weather + health alerts pulled from the
 * account-wide weather feed.
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
import { CalloutInline } from '../../../components/callouts';
import { useVehicleCallouts } from './_shared/useVehicle';
import type { VehicleSectionProps } from './_shared/types';
import { Card } from '@/components/ui/card';

interface CabinWeather {
  temp_f: number | null;
  temp_c: number | null;
  baro_inhg: number | null;
  temp_time: string | null;
  baro_time: string | null;
}

interface FleetWeatherEntry {
  name: string;
  temp_f: number | null;
  temp_c: number | null;
  baro_inhg: number | null;
  temp_time: string | null;
  baro_time?: string | null;
}

interface FleetWeatherResponse {
  vehicles: FleetWeatherEntry[];
}

export default function VehicleHealth({ vehicleName, company }: VehicleSectionProps) {
  const engineCallouts = useVehicleCallouts(vehicleName, company);
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

  // Cabin weather lives in the account-wide /vehicles/weather feed.  No
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
            baro_time: entry.baro_time ?? null,
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
  // Every row below except Battery rides the engine bus, so when the
  // device cannot read it they are ALL empty for one reason.  Six
  // bare dashes invite six guesses; the callout answers once.
  const blind = engineCallouts.find(
    (c) => c.key === 'vehicle.no_engine_data',
  ) ?? null;
  // Takes the VALUE and a formatter, never a pre-built element: an
  // element argument is evaluated before the call, so a `ready` flag
  // cannot protect the formatting inside it — `oil_psi!.toFixed(1)`
  // ran on null and took the whole card down with it.  Passing the
  // raw value makes the guard structural, and drops the non-null
  // assertions that told the type-checker to look away.
  function busValue<T>(
    value: T | null | undefined, fmt: (v: T) => string,
  ): React.ReactNode {
    if (value != null) return <span>{fmt(value)}</span>;
    // ``explained``: the page's strip (VehicleHeader) already
    // carries the paragraph — six more copies behind six empty
    // sensors is noise, not help.
    return blind
      ? <CalloutInline callout={blind} explained />
      : <span>—</span>;
  }
  const healthAlerts: string[] = health.alerts || [];

  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold mb-3">Vehicle Health</h2>
      {/* Each sensor carries its OWN Samsara clock — a dead oil-pressure
          sensor freezes independently of the rest of the card. */}
      <Row label="Battery" ts={h.battery_time} value={h.battery_v != null ? `${h.battery_v.toFixed(1)} V` : '—'} />
      <Row label="Oil Pressure" ts={h.oil_time}>
        {busValue(h.oil_psi, (v) => `${v.toFixed(1)} PSI`)}
      </Row>
      <Row label="Coolant Temp" ts={h.coolant_time}>
        {busValue(h.coolant_c, (v) => `${v.toFixed(0)}°C`)}
      </Row>
      <Row label="DEF Level" ts={h.def_time}>
        {busValue(h.def_pct, (v) => `${v.toFixed(0)}%`)}
      </Row>
      <Row label="Engine Load" ts={h.load_time}>
        {busValue(h.load_pct, (v) => `${v.toFixed(0)}%`)}
      </Row>
      <Row label="RPM" ts={h.rpm_time}>
        {busValue(h.rpm, (v) => String(Math.round(v)))}
      </Row>
      <Row label="Seatbelt" ts={h.seatbelt_time}>
        {busValue(h.seatbelt, (v) => String(v))}
      </Row>
      {weather && (weather.temp_f != null || weather.baro_inhg != null) && (
        <>
          <Row
            label="Cabin Temp"
            ts={weather.temp_time}
            value={weather.temp_f != null ? `${weather.temp_f}°F` : '—'}
          />
          <Row
            label="Barometer"
            ts={weather.baro_time}
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
              className={`inline-block text-xs rounded-md px-2 py-0.5 mr-1 mb-1 ${toneClasses('danger')}`}
            >
              {a}
            </span>
          ))}
        </div>
      )}
    </Card>
  );
}
