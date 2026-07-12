import { describe, it, expect } from 'vitest';
import { extractVehicleRefs } from './ReferencedVehicles';

// `tool_results` shape: [{ tool, args, data }], where `data` is the tool's
// return dict.  extractVehicleRefs pulls a deduped, severity-ranked vehicle
// list out of it for the clickable chips.

function tr(tool: string, data: unknown) {
  return { tool, args: {}, data };
}

describe('extractVehicleRefs', () => {
  it('returns [] for non-array / empty / vehicle-less results', () => {
    expect(extractVehicleRefs(undefined)).toEqual([]);
    expect(extractVehicleRefs([])).toEqual([]);
    expect(extractVehicleRefs([tr('get_weather', { forecast: 'sunny' })])).toEqual([]);
  });

  it('extracts undriven vehicles with days_stopped severity + company', () => {
    const refs = extractVehicleRefs([
      tr('get_undriven_vehicles', {
        vehicles: [
          { vehicle: 'B-1', company: 'B', days_stopped: 9 },   // >=7 → danger
          { vehicle: 'B-2', company: 'B', days_stopped: 4 },   // >=3 → warn
          { vehicle: 'B-3', company: 'B', days_stopped: 1 },   // <3  → info
        ],
      }),
    ]);
    const by = Object.fromEntries(refs.map((r) => [r.vehicle, r]));
    expect(by['B-1'].tone).toBe('danger');
    expect(by['B-1'].company).toBe('B');
    expect(by['B-2'].tone).toBe('warn');
    expect(by['B-3'].tone).toBe('info');
    expect(by['B-1'].note).toContain('stopped');
  });

  it('maps alert severity to tone', () => {
    const refs = extractVehicleRefs([
      tr('get_alert_history', {
        alerts: [
          { vehicle: 'A-1', severity: 'critical', type: 'engine_fault' },
          { vehicle: 'A-2', severity: 'info', type: 'geofence' },
        ],
      }),
    ]);
    const by = Object.fromEntries(refs.map((r) => [r.vehicle, r]));
    expect(by['A-1'].tone).toBe('danger');
    expect(by['A-2'].tone).toBe('info');
    expect(by['A-1'].note).toBe('engine fault'); // underscores humanised
  });

  it('classifies low-fuel and parked rows', () => {
    const refs = extractVehicleRefs([
      tr('get_low_fuel_vehicles', { vehicles: [{ vehicle: 'F-1', fuel_pct: 10 }] }),
      tr('get_parked_vehicles', { vehicles: [{ vehicle: 'P-1', company: 'C', duration_days: 6 }] }),
    ]);
    const by = Object.fromEntries(refs.map((r) => [r.vehicle, r]));
    expect(by['F-1'].tone).toBe('danger');      // <15% fuel
    expect(by['F-1'].note).toContain('fuel');
    expect(by['P-1'].tone).toBe('danger');       // parked >=5d
    expect(by['P-1'].company).toBe('C');
  });

  it('classifies maintenance overdue/due-soon/pending task rows', () => {
    const refs = extractVehicleRefs([
      tr('get_maintenance_summary', {
        total_pending: 1, total_due_soon: 1, total_overdue: 1,
        overdue_tasks: [{ vehicle: 'Truck 9', type: 'brake_inspection' }],
        due_soon_tasks: [{ vehicle: 'Truck 233', type: 'oil_change' }],
        pending_tasks: [{ vehicle: 'Truck 212', type: 'oil_change' }],
      }),
    ]);
    const by = Object.fromEntries(refs.map((r) => [r.vehicle, r]));
    expect(by['Truck 9'].tone).toBe('danger');          // overdue
    expect(by['Truck 9'].note).toBe('overdue brake inspection');
    expect(by['Truck 233'].tone).toBe('warn');          // due soon
    expect(by['Truck 233'].note).toBe('oil change due soon');
    expect(by['Truck 212'].tone).toBe('info');          // pending
    expect(by['Truck 212'].note).toBe('due oil change');
  });

  it('classifies rolling/idling/off lists', () => {
    const refs = extractVehicleRefs([
      tr('get_rolling_stopped', {
        rolling_vehicles: [{ vehicle: 'R-1' }],
        idling_vehicles: [{ vehicle: 'I-1' }],
        off_vehicles: [{ vehicle: 'O-1' }],
      }),
    ]);
    const by = Object.fromEntries(refs.map((r) => [r.vehicle, r]));
    expect(by['R-1'].tone).toBe('ok');
    expect(by['I-1'].tone).toBe('warn');
    expect(by['O-1'].tone).toBe('neutral');
  });

  it('dedupes a vehicle across tools, keeping the highest severity', () => {
    const refs = extractVehicleRefs([
      tr('get_rolling_stopped', { off_vehicles: [{ vehicle: 'X-1' }] }),         // neutral
      tr('get_alert_history', { alerts: [{ vehicle: 'X-1', severity: 'critical' }] }), // danger
    ]);
    const x1 = refs.filter((r) => r.vehicle === 'X-1');
    expect(x1).toHaveLength(1);
    expect(x1[0].tone).toBe('danger');
  });

  it('carries a company code forward even from a lower-severity row', () => {
    const refs = extractVehicleRefs([
      tr('get_alert_history', { alerts: [{ vehicle: 'Y-1', severity: 'critical' }] }), // danger, no company
      tr('get_undriven_vehicles', { vehicles: [{ vehicle: 'Y-1', company: 'B', days_stopped: 1 }] }), // info, has company
    ]);
    const y1 = refs.find((r) => r.vehicle === 'Y-1')!;
    expect(y1.tone).toBe('danger');   // severity from the alert
    expect(y1.company).toBe('B');     // company from the undriven row
  });
});
