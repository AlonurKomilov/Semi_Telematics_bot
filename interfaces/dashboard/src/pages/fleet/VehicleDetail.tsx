import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import StatusBadge from '../../components/StatusBadge';
import { usePermissions } from '../../hooks/usePermissions';
import type { Vehicle, VehiclesResponse, HealthResponse, FaultsResponse, Fault, HealthData } from '../../types';

export default function VehicleDetail() {
  const { name } = useParams<{ name: string }>();
  const { has } = usePermissions();
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [faults, setFaults] = useState<FaultsResponse | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const encoded = encodeURIComponent(name!);
    apiJSON<VehiclesResponse>(`/fleet/vehicle/${encoded}`)
      .then((d) => {
        if (d.vehicles?.length) setVehicle(d.vehicles[0]);
        else setError('Vehicle not found');
      })
      .catch((e) => setError(e.message));

    if (has('can_health')) {
      apiJSON<HealthResponse>(`/fleet/vehicle/${encoded}/health`)
        .then((d) => { if (d.health) setHealth(d); })
        .catch(() => {});
    }

    if (has('can_faults')) {
      apiJSON<FaultsResponse>(`/fleet/vehicle/${encoded}/faults`)
        .then(setFaults)
        .catch(() => {});
    }
  }, [name, has]);

  if (error) return <p className="text-destructive">{error}</p>;
  if (!vehicle) return <p className="text-muted-foreground">Loading...</p>;

  const v = vehicle;
  const loc = v.location || {};
  // Backend normalizes fuel/def/speed/engineState into both flat and nested forms.
  // fuelPercent / defPercent are added by _normalize_detail() in fleet.py.
  const fuel = v.fuelPercent ?? v.fuel_percent;
  const defPct = v.defPercent ?? v.def_percent;
  // faults endpoint returns DTC list directly; fallback to fault_codes on vehicle dict.
  const faultList: Fault[] = faults?.faults || [];
  const h: HealthData = health?.health || {};
  const healthAlerts: string[] = health?.alerts || [];

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
            <h2 className="text-lg font-semibold mb-3">
              Active Fault Codes ({faultList.length})
            </h2>
            <div className="space-y-2">
              {faultList.map((f: Record<string, unknown>, i) => {
                // Raw Samsara DTCs have fields at the top level;
                // some older data may wrap them in a j1939 sub-object.
                const j = (f.j1939 as Record<string, unknown> | undefined) ?? {};
                const spn   = (j.spnDescription   ?? f.spnDescription   ?? f.code ?? 'DTC') as string;
                const fmi   = (j.fmiDescription   ?? f.fmiDescription)  as string | undefined;
                const src   = (j.sourceAddressName ?? f.sourceAddressName) as string | undefined;
                const count = (f.occurrences ?? f.occurrenceCount)       as number | undefined;
                const desc  = f.description as string | undefined;
                return (
                <div key={i} className="bg-muted rounded-lg p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-orange-400">{spn}</span>
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
          </div>
        )}

        {faultList.length === 0 && faults && (
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-lg font-semibold mb-3">Fault Codes</h2>
            <p className="text-green-400 text-sm">No active fault codes</p>
          </div>
        )}
      </div>
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
