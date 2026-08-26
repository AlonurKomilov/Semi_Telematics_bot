/**
 * Device questions render through the callouts lane — display only.
 *
 * The identity watch keeps its own store, its own detector and its own
 * answer flow: "Different truck…" performs registry surgery, which the
 * callouts capability must never learn about.  What moved is the
 * SHAPE, so a page does not carry two kinds of statement.
 */
import { describe, it, expect } from 'vitest';
import { CALLOUT_CATALOG, dismissBehaviour } from '../../components/callouts';
import { subjectUnit } from './deviceEvents';

const EVENT_KEYS = [
  'vehicle.vin_changed',
  'vehicle.gateway_swapped',
  'vehicle.odometer_rebased',
];

describe('device-event callouts', () => {
  it('are registered in the catalog', () => {
    // Unregistered keys render as raw strings; the drift guard in
    // test_callouts.py enforces the backend half of the same seam.
    for (const key of EVENT_KEYS) {
      expect(CALLOUT_CATALOG[key]).toBeDefined();
    }
  });

  it('fold, but are never removed — the question stays on screen', () => {
    // The fear this once encoded was real and aimed at the wrong
    // control: hiding a pending identity question would leave a
    // truck's history filed under the wrong unit with nothing on
    // screen to say so.  Removal would do that.  Collapse does not —
    // it leaves the statement as one line carrying its count, re-opens
    // when a new truck raises the same question, and undoes in a
    // click.  Someone who is not the person answering these should not
    // carry the queue at the top of their page all week.
    for (const key of EVENT_KEYS) {
      expect(dismissBehaviour(key)).toBe('collapse');
    }
    // The guarantee that matters is that 'remove' does not exist at
    // all — no callout can be hidden outright, whatever it declares.
    const behaviours = Object.keys(CALLOUT_CATALOG)
      .map((k) => dismissBehaviour(k));
    expect(behaviours).not.toContain('remove');
  });

  it('asks in warn, never danger', () => {
    // Danger is reserved for states that are actively failing (overdue,
    // past-due, error).  These are questions waiting on a person, and
    // a full red strip both overstated them and made the body text
    // hard to read against its own background.
    for (const key of EVENT_KEYS) {
      expect(CALLOUT_CATALOG[key].severity).toBe('warn');
    }
  });
});

/**
 * Which truck the strip NAMES must be the truck the buttons EDIT.
 *
 * The event log stores the provider's label from ingest time, and it
 * does not have to agree with the registry.  In a live account it did
 * not: an open VIN question displayed "128" while its `registry_id`
 * pointed at unit 6862, and another displayed "254" pointing at unit
 * 253.  Both answers act on `registry_id` — "Different truck…" runs
 * `split_vehicle_identity` against it, minting a unit and forking that
 * truck's history.  Someone would have split 6862 believing they were
 * answering a question about 128, and there is no undo.
 *
 * (The root cause is a data one — four telematics refs claimed by two
 * registry rows each — and it is being cleaned separately.  This is
 * the display half: whatever the data says, the label and the action
 * must point at the same row.)
 */
describe('the truck a device question names', () => {
  const unitByRegistryId = new Map<number, string>([
    [16, '6862'],
    [11057, '253'],
    [60, '229'],
  ]);

  it('is the registry unit, not the provider label', () => {
    expect(subjectUnit(
      { registry_id: 16, vehicle_name: '128', vehicle_id: '281475003801071' },
      unitByRegistryId,
    )).toBe('6862');
    expect(subjectUnit(
      { registry_id: 11057, vehicle_name: '254', vehicle_id: 'x' },
      unitByRegistryId,
    )).toBe('253');
  });

  it('agrees with the provider when the provider agrees', () => {
    expect(subjectUnit(
      { registry_id: 60, vehicle_name: '229 Idris Ahmed', vehicle_id: 'y' },
      unitByRegistryId,
    )).toBe('229');
  });

  it('falls back to the provider label when no registry row matches', () => {
    // An event predating registry stamping, or a row since removed.
    // Nothing better exists, and a blank subject is worse than an
    // imperfect one.
    expect(subjectUnit(
      { registry_id: null, vehicle_name: '130', vehicle_id: 'z' },
      unitByRegistryId,
    )).toBe('130');
    expect(subjectUnit(
      { registry_id: 999, vehicle_name: '', vehicle_id: 'telematics-z' },
      unitByRegistryId,
    )).toBe('telematics-z');
  });
});
