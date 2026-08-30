/**
 * Add vehicle — the dialog must name the RIGHT truck.
 *
 * The registry mirrors every provider vehicle, so "does Samsara
 * already have this truck?" is answerable instantly from rows the page
 * already holds — no provider round-trip.  What that answer must never
 * do is guess: a unit number is a LABEL, reused across companies (this
 * account runs 001 in two and 103 in three), and the first version
 * matched on the number ALONE, so typing 103 offered whichever 103 the
 * list happened to hold first.  The first test below fails on that
 * rendering.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

afterEach(cleanup);

vi.mock('../../api/client', () => ({ apiJSON: vi.fn() }));
vi.mock('../../components/activity-trail/ActivityTrailDialog', () => ({
  ActivityTrailDialog: () => null,
  ActivityTrailTrigger: () => null,
}));

import VehicleManageDialog from './VehicleManageDialog';
import type { Vehicle } from '../../types';

const row = (o: Partial<Vehicle>) => o as Vehicle;

// Three trucks share the door number 103, exactly as they do in
// production; 247 is retired; 900 carries a known VIN.
const REGISTRY: Vehicle[] = [
  row({ name: '103', company: 'G1', registry_id: 1, sources: ['samsara'] }),
  row({ name: '103', company: 'OSY', registry_id: 2, sources: ['samsara'] }),
  row({ name: '103', company: 'PTG', registry_id: 3, sources: ['datatruck'] }),
  row({ name: '247', company: 'PTG', registry_id: 4, archived: true,
        sources: ['samsara'] }),
  row({ name: '900', company: 'CFT', registry_id: 5,
        vin: '1HGTEST0000000009', sources: ['samsara'] }),
];

const open = (rows: Vehicle[] = REGISTRY) =>
  render(
    <VehicleManageDialog
      open vehicle={null} existingVehicles={rows}
      onClose={() => {}} onSaved={() => {}}
    />,
  );

const type = (placeholder: string, value: string) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), {
    target: { value },
  });

const body = () => document.body.textContent ?? '';

describe('Add vehicle — matching the typed identity', () => {
  it('will not pick a truck when the unit number names three', () => {
    open();
    type('247', '103');
    expect(body()).toMatch(/3 vehicles are numbered/);
    expect(body()).toMatch(/Type the company code/);
    // The failure this guards: claiming ONE match, which is what
    // offering "Use its details" here would mean.
    expect(body()).not.toMatch(/already exists/);
    expect(screen.queryByRole('button', { name: /use its details/i }))
      .toBeNull();
  });

  it('names the company that was typed, not the first row that matched', () => {
    open();
    type('247', '103');
    type('PTG', 'OSY');
    expect(body()).toMatch(/already exists/);
    expect(body()).toMatch(/OSY/);
    // G1 holds a 103 too and sits FIRST in the list — the company-blind
    // match offered it.
    expect(body()).not.toMatch(/G1/);
  });

  it('offers Restore for an archived unit instead of a second row', () => {
    open();
    type('247', '247');
    type('PTG', 'PTG');
    expect(body()).toMatch(/is archived/);
    expect(screen.getByRole('button', { name: /restore/i })).toBeTruthy();
    // Adding would collide on (company, unit) anyway — so the dialog
    // must not present adding as the way forward.
    expect(body()).not.toMatch(/already exists/);
  });

  it('catches the same truck arriving under a different number, by VIN', () => {
    open();
    type('247', '901');
    type('1HGTEST0000000001', '1HGTEST0000000009');
    expect(body()).toMatch(/same truck, under another number/);
    expect(body()).toMatch(/900/);
  });

  it('does not claim a VIN match while the VIN is still being typed', () => {
    open();
    type('247', '901');
    type('1HGTEST0000000001', '1HGTEST');
    expect(body()).not.toMatch(/same truck/);
  });

  it('says what happens next, naming the providers that really supply', () => {
    open();
    type('247', '901');
    expect(body()).toMatch(/Datatruck and Samsara supply vehicles here/);
    expect(body()).toMatch(/links automatically/);
  });

  it('suggests the company codes this operator can actually mean', () => {
    open();
    const codes = [...document.querySelectorAll('#registry-companies option')]
      .map((o) => (o as HTMLOptionElement).value);
    expect(codes).toEqual(['CFT', 'G1', 'OSY', 'PTG']);
  });

  it('fills the company when only one is possible, asks when several are', () => {
    // The company-restricted operator's case: the API scopes their rows
    // to one code, and it is also the field the API REFUSES to accept
    // blank from them — so it must not arrive empty.
    open([row({ name: '5', company: 'PTG', registry_id: 9 })]);
    expect((screen.getByPlaceholderText('PTG') as HTMLInputElement).value)
      .toBe('PTG');
    cleanup();
    // Several companies: guessing one would be worse than asking.
    open();
    expect((screen.getByPlaceholderText('PTG') as HTMLInputElement).value)
      .toBe('');
  });

  it('promises nothing when no integration supplies vehicles', () => {
    open([row({ name: '5', company: 'PTG', registry_id: 9,
                sources: ['manual'] })]);
    type('247', '901');
    expect(body()).toMatch(/No integration supplies vehicles here yet/);
    expect(body()).toMatch(/stays Local/);
  });
});
