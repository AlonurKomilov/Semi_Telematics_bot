import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X, AlertTriangle } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { VehiclePicker, type FleetVehicle } from '../maintenance/pickers';

/**
 * Fleet-driven ad-hoc inspection creation.
 *
 * Vehicle-first design: the fleet thinks "inspect Truck 107", not
 * "make this driver inspect something".  The vehicle picker (same
 * component the Maintenance page uses) is the primary input;
 * the driver auto-resolves from whichever driver currently has the
 * truck assigned in ``driver_vehicle_assignments``.
 *
 * Fleet can still override the driver pick when:
 *   * Multiple drivers share a truck (rare, but supported).
 *   * The vehicle has no assigned driver and an ad-hoc inspector
 *     (yard mechanic, fleet manager) is doing the walkaround.
 *
 * Server enforces: driver belongs to the account, target template
 * exists, the driver doesn't already have an open inspection.  On
 * success, the bot fires an immediate reminder so the driver knows
 * it's been assigned right now.
 */

interface DriverOption {
  user_id: number;
  display_name: string;
  vehicle_name: string | null;
}

interface DriversResponse {
  drivers: DriverOption[];
}

interface VehiclesResponse {
  vehicles: FleetVehicle[];
}

interface Props {
  onCreated: () => void;
  onClose: () => void;
}

type InspectionType = 'weekly' | 'monthly' | 'daily_pre_trip' | 'ad_hoc';
type VehicleType = 'truck' | 'trailer';


function _daysFromNowIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString();
}


export function NewInspectionDialog({ onCreated, onClose }: Props) {
  const { t } = useTranslation();
  const [vehicleName, setVehicleName] = useState('');
  const [driverId, setDriverId] = useState<number | null>(null);
  const [driverOverridden, setDriverOverridden] = useState(false);
  const [inspectionType, setInspectionType] = useState<InspectionType>('weekly');
  const [vehicleType, setVehicleType] = useState<VehicleType>('truck');
  const [dueDays, setDueDays] = useState(7);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: fleet, isLoading: loadingFleet } = useQuery({
    queryKey: ['pti-vehicle-picker'],
    queryFn: () => apiJSON<VehiclesResponse>('/vehicles?page_size=200'),
  });
  const vehicles = fleet?.vehicles ?? [];

  const { data: drivers, isLoading: loadingDrivers } = useQuery({
    queryKey: ['pti-driver-picker'],
    queryFn: () => apiJSON<DriversResponse>('/drivers'),
  });

  // Build a {lowercase_vehicle_name: driver} lookup so picking a
  // vehicle instantly resolves which driver gets the inspection.
  // Case-insensitive because dispatch tools occasionally store "107"
  // vs "Truck-107" — we match on whichever the driver_vehicle_
  // assignment row has.
  const driverByVehicle = useMemo(() => {
    const out: Record<string, DriverOption> = {};
    for (const d of drivers?.drivers ?? []) {
      if (d.vehicle_name) {
        out[d.vehicle_name.toLowerCase()] = d;
      }
    }
    return out;
  }, [drivers]);

  // Auto-resolve driver when the user picks a vehicle.  If the fleet
  // later manually overrides the driver (e.g. ad-hoc inspector on an
  // unassigned truck), ``driverOverridden`` keeps the override
  // sticky — we don't re-snap back to the assigned driver.
  useEffect(() => {
    if (driverOverridden) return;
    if (!vehicleName.trim()) {
      setDriverId(null);
      return;
    }
    const d = driverByVehicle[vehicleName.trim().toLowerCase()];
    setDriverId(d?.user_id ?? null);
  }, [vehicleName, driverByVehicle, driverOverridden]);

  const resolvedDriver = useMemo(() => {
    if (driverId == null) return null;
    return drivers?.drivers.find(d => d.user_id === driverId) ?? null;
  }, [driverId, drivers]);

  const submit = async () => {
    if (!vehicleName.trim()) {
      setError(t('inspections.new.vehicle_required'));
      return;
    }
    if (driverId == null) {
      setError(t('inspections.new.driver_required'));
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await apiJSON('/inspections', {
        method: 'POST',
        body: {
          driver_id: driverId,
          vehicle_name: vehicleName.trim(),
          inspection_type: inspectionType,
          vehicle_type: vehicleType,
          due_by: _daysFromNowIso(dueDays),
        },
      });
      toast.success(t('inspections.new.created'));
      onCreated();
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('inspections.new.create_failed');
      setError(msg);
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-start justify-center pt-16 sm:pt-24"
      onClick={onClose}
    >
      <div
        className="w-[480px] max-w-full bg-card border border-border rounded-xl p-5 shadow-2xl space-y-3"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">{t('inspections.new.title')}</h3>
          <button onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X size={16} />
          </button>
        </div>

        {/* Vehicle picker — primary input.  Searchable, shows status
            dot + company + fuel% so fleet can pick at-a-glance from
            the whole fleet without leaving the modal. */}
        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">
            {t('inspections.new.vehicle')}
          </span>
          <VehiclePicker
            value={vehicleName}
            vehicles={vehicles}
            loading={loadingFleet}
            onChange={(name) => {
              setVehicleName(name);
              setDriverOverridden(false);  // re-enable auto-resolve
              setError(null);
            }}
          />
        </label>

        {/* Driver — auto-resolved from the picked vehicle.  Override
            via the dropdown when the vehicle has no assigned driver
            (e.g. yard truck) or fleet wants a different driver to do
            the walkaround. */}
        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">
            {t('inspections.new.driver')}
            {resolvedDriver && !driverOverridden && (
              <span className="ml-1 text-green-600 dark:text-green-400">
                (auto: {resolvedDriver.display_name || `user ${resolvedDriver.user_id}`})
              </span>
            )}
          </span>
          <select
            value={driverId ?? ''}
            onChange={e => {
              setDriverId(e.target.value ? Number(e.target.value) : null);
              setDriverOverridden(true);
            }}
            disabled={loadingDrivers || saving}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
          >
            <option value="">
              {loadingDrivers ? t('common.loading') : t('inspections.new.driver_picker_placeholder')}
            </option>
            {drivers?.drivers.map(d => (
              <option key={d.user_id} value={d.user_id}>
                {d.display_name || `user ${d.user_id}`}
                {d.vehicle_name ? ` · ${d.vehicle_name}` : ''}
              </option>
            ))}
          </select>
          {vehicleName.trim() && driverId == null && !loadingDrivers && (
            <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
              <AlertTriangle size={11} />
              {t('inspections.new.no_driver_for_vehicle')}
            </span>
          )}
        </label>

        {/* Inspection type */}
        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">{t('inspections.new.type')}</span>
          <select
            value={inspectionType}
            onChange={e => setInspectionType(e.target.value as InspectionType)}
            disabled={saving}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
          >
            <option value="weekly">{t('inspections.new.type_weekly')}</option>
            <option value="monthly">{t('inspections.new.type_monthly')}</option>
            <option value="daily_pre_trip">{t('inspections.new.type_daily')}</option>
            <option value="ad_hoc">{t('inspections.new.type_adhoc')}</option>
          </select>
        </label>

        {/* Vehicle type — picks which template the items snapshot from */}
        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">
            {t('inspections.new.template')}
          </span>
          <select
            value={vehicleType}
            onChange={e => setVehicleType(e.target.value as VehicleType)}
            disabled={saving}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
          >
            <option value="truck">{t('inspections.tab_truck')}</option>
            <option value="trailer">{t('inspections.tab_trailer')}</option>
          </select>
        </label>

        {/* Due period */}
        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">
            {t('inspections.new.due_in')}
          </span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={90}
              value={dueDays}
              onChange={e => setDueDays(Math.max(1, Math.min(90, Number(e.target.value) || 1)))}
              disabled={saving}
              className="w-20 bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
            />
            <span className="text-muted-foreground">{t('inspections.new.days')}</span>
            <div className="ml-auto flex gap-1">
              {[1, 3, 7, 14, 30].map(d => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDueDays(d)}
                  disabled={saving}
                  className={`px-1.5 py-0.5 text-xs border rounded ${
                    dueDays === d ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'
                  }`}
                >
                  +{d}d
                </button>
              ))}
            </div>
          </div>
        </label>

        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={saving || !vehicleName.trim() || driverId == null}
            className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? t('common.saving') : t('inspections.new.assign')}
          </button>
        </div>
      </div>
    </div>
  );
}
