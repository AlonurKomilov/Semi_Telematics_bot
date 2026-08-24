/**
 * VehicleHealth — the null-sensor render.
 *
 * A truck whose device cannot read the engine bus reports NOTHING for
 * six of these seven rows.  That is the normal state for such a truck,
 * not an edge case — and it took the whole card down in production:
 * the callout helper was handed a pre-built `<span>{v!.toFixed(1)}</span>`,
 * which JavaScript evaluates BEFORE the guard that was supposed to
 * protect it, so `toFixed` ran on null.  The non-null assertions are
 * what kept the type-checker quiet about it.
 *
 * These tests render the card with the real shape of that truck's
 * data, which is the only thing that would have caught it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

afterEach(cleanup);

const mockHealth = vi.fn();
vi.mock('@tanstack/react-query', () => ({
  useQuery: (opts: { queryKey: unknown[] }) => mockHealth(opts.queryKey),
}));
vi.mock('../../../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({ has: () => true }),
}));
const mockCallouts = vi.fn(() => [] as unknown[]);
vi.mock('./_shared/useVehicle', () => ({
  useVehicleCallouts: () => mockCallouts(),
  useVehicle: () => ({ vehicle: null, isLoading: false }),
}));

import VehicleHealth from './VehicleHealth';

/** Every engine-bus sensor silent — truck 548640's real shape. */
const ALL_NULL = {
  battery_v: 14.1, battery_time: null,
  oil_psi: null, oil_time: null,
  coolant_c: null, coolant_time: null,
  def_pct: null, def_time: null,
  load_pct: null, load_time: null,
  rpm: null, rpm_time: null,
  seatbelt: null, seatbelt_time: null,
};

function arrange(health: Record<string, unknown>, callouts: unknown[] = []) {
  mockCallouts.mockReturnValue(callouts);
  mockHealth.mockImplementation((key: unknown[]) =>
    String(key[0]) === 'vehicle-health'
      ? { data: { health, alerts: [] }, isLoading: false }
      : { data: null, isLoading: false },
  );
}

describe('VehicleHealth with a silent engine bus', () => {
  it('renders instead of throwing when every sensor is null', () => {
    arrange(ALL_NULL);
    render(<VehicleHealth vehicleName="548640" company="OSY" />);
    // Battery is NOT on the engine bus — it still reports.
    expect(screen.getByText('14.1 V')).toBeTruthy();
    expect(screen.getByText('Oil Pressure')).toBeTruthy();
  });

  it('explains the empty rows when the truck has the callout', () => {
    arrange(ALL_NULL, [{ key: 'vehicle.no_engine_data' }]);
    render(<VehicleHealth vehicleName="548640" company="OSY" />);
    // One explanation per silent bus row (6), never on Battery.
    expect(screen.getAllByText(/No engine data/i).length).toBe(6);
  });

  it('falls back to a plain dash when there is no callout', () => {
    arrange(ALL_NULL);
    render(<VehicleHealth vehicleName="123" company="OSY" />);
    expect(screen.queryByText(/No engine data/i)).toBeNull();
  });

  it('still formats real readings', () => {
    arrange({ ...ALL_NULL, oil_psi: 42.35, rpm: 1499.6, def_pct: 87.4 });
    render(<VehicleHealth vehicleName="128" company="PTG" />);
    expect(screen.getByText('42.4 PSI')).toBeTruthy();
    expect(screen.getByText('1500')).toBeTruthy();
    expect(screen.getByText('87%')).toBeTruthy();
  });
});
