import { describe, expect, it } from 'vitest';
import { MAP_STATUS } from '../../config/mapColors';
import { faceOf, iconSignature, statusColour } from './markerFace';

const parked = { isMoving: false, engineState: 'Off' };

describe('faceOf', () => {
  it('takes the list when the fast poll has not reached this truck', () => {
    expect(faceOf('moving', undefined)).toEqual({ colour: MAP_STATUS.ok, moving: true });
    expect(faceOf('idle', undefined)).toEqual({ colour: MAP_STATUS.warn, moving: false });
    expect(faceOf('stopped', undefined)).toEqual({ colour: MAP_STATUS.danger, moving: false });
  });

  it('lets the fresher feed overrule a stale list', () => {
    // The thirty-second list still says stopped; the five-second poll
    // has this truck moving.  It is moving.
    expect(faceOf('stopped', { isMoving: true, engineState: 'On' }))
      .toEqual({ colour: MAP_STATUS.ok, moving: true });
    // And the other way round.
    expect(faceOf('moving', parked)).toEqual({ colour: MAP_STATUS.danger, moving: false });
  });

  it('separates a truck idling from one switched off', () => {
    expect(faceOf('stopped', { isMoving: false, engineState: 'Idle' }).colour).toBe(MAP_STATUS.warn);
    expect(faceOf('stopped', { isMoving: false, engineState: 'On' }).colour).toBe(MAP_STATUS.warn);
    expect(faceOf('stopped', parked).colour).toBe(MAP_STATUS.danger);
  });

  it('agrees with the list-only colour helper when there is no motion', () => {
    for (const s of ['moving', 'idle', 'stopped'] as const) {
      expect(faceOf(s, undefined).colour).toBe(statusColour(s));
    }
  });
});

describe('iconSignature', () => {
  it('is the same for two pictures that would look the same', () => {
    // A moving truck is an arrow, and an arrow has no low-level ring —
    // so the warning cannot change what is drawn while it moves.
    expect(iconSignature(MAP_STATUS.ok, false, true)).toBe(iconSignature(MAP_STATUS.ok, true, true));
  });

  it('separates every difference a person can see', () => {
    const seen = new Set([
      iconSignature(MAP_STATUS.ok, false, true),
      iconSignature(MAP_STATUS.warn, false, false),
      iconSignature(MAP_STATUS.danger, false, false),
      iconSignature(MAP_STATUS.danger, true, false),
    ]);
    expect(seen.size).toBe(4);
  });
});
