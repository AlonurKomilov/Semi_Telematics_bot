/**
 * VehicleInfo — the Engine row's three answers.
 *
 * The row used to default to "Off", which was a claim rather than a
 * reading: the ingest deliberately stores EMPTY for a truck whose
 * device cannot read the engine bus (so the roll-ups never count
 * silence as parked), and the UI restated that silence as fact.
 *
 * The replacement is not "unknown" either.  We are not confused about
 * the engine — we know nothing arrived, and the rest of the page
 * already says exactly that.  One fact, one wording.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

afterEach(cleanup);

const mockVehicle = vi.fn();
const mockCallouts = vi.fn(() => [] as unknown[]);
vi.mock('./_shared/useVehicle', () => ({
  useVehicle: () => mockVehicle(),
  useVehicleCallouts: () => mockCallouts(),
}));
vi.mock('../../../components/callouts', () => ({
  CalloutInline: () => <span>— No data</span>,
}));

import VehicleInfo from './VehicleInfo';

const BASE = {
  vin: 'X', make: 'FREIGHTLINER', model: 'CASCADIA', year: 2022,
  company: 'OSY', location: { time: '' },
};

function arrange(vehicle: Record<string, unknown>, callouts: unknown[] = []) {
  mockCallouts.mockReturnValue(callouts);
  mockVehicle.mockReturnValue({ vehicle, isLoading: false });
}

describe('VehicleInfo Engine row', () => {
  it('shows what the device reported', () => {
    arrange({ ...BASE, engine_state: 'Idle' });
    render(<VehicleInfo vehicleName="103" company="G1" />);
    expect(screen.getByText('Idle')).toBeTruthy();
  });

  it('never invents "Off" when nothing was reported', () => {
    arrange({ ...BASE, engine_state: '' });
    render(<VehicleInfo vehicleName="548640" company="OSY" />);
    expect(screen.queryByText('Off')).toBeNull();
  });

  it('says "no data" — the fact — when there is no callout', () => {
    arrange({ ...BASE, engine_state: '' });
    render(<VehicleInfo vehicleName="548640" company="OSY" />);
    expect(screen.getByText('no data')).toBeTruthy();
  });

  it('speaks the page vocabulary when the callout explains it', () => {
    arrange({ ...BASE, engine_state: '' }, [{ key: 'vehicle.no_engine_data' }]);
    render(<VehicleInfo vehicleName="548640" company="OSY" />);
    // Same words as Fuel / Odometer / Engine Hours below it — the row
    // must not introduce a third vocabulary for one fact.
    expect(screen.getAllByText('— No data').length).toBeGreaterThan(1);
    expect(screen.queryByText('no data')).toBeNull();
  });
});
