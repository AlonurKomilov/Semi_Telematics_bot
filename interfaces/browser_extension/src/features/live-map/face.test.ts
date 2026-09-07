import { describe, expect, it } from 'vitest';
import { MAP_STATUS, faceOf, type Phys } from './physics';
import { iconSignature } from './icons';

function phys(over: Partial<Phys>): Pick<Phys, 'isMoving' | 'engineState'> {
  return { isMoving: false, engineState: 'Off', ...over };
}

describe('faceOf', () => {
  it('takes the list when the fast poll has not reached this truck', () => {
    expect(faceOf('moving', undefined)).toEqual({ colour: MAP_STATUS.ok, moving: true });
    expect(faceOf('idle', undefined)).toEqual({ colour: MAP_STATUS.warn, moving: false });
    expect(faceOf('stopped', undefined)).toEqual({ colour: MAP_STATUS.danger, moving: false });
  });

  it('lets the fresher feed overrule a stale list', () => {
    // The list still says stopped; the five-second poll has it moving.
    expect(faceOf('stopped', phys({ isMoving: true }))).toEqual({ colour: MAP_STATUS.ok, moving: true });
    // And the other way: parked, though the list has not caught up.
    expect(faceOf('moving', phys({ isMoving: false, engineState: 'Off' })))
      .toEqual({ colour: MAP_STATUS.danger, moving: false });
  });

  it('separates a truck idling from one switched off', () => {
    expect(faceOf('stopped', phys({ engineState: 'Idle' })).colour).toBe(MAP_STATUS.warn);
    expect(faceOf('stopped', phys({ engineState: 'On' })).colour).toBe(MAP_STATUS.warn);
    expect(faceOf('stopped', phys({ engineState: 'Off' })).colour).toBe(MAP_STATUS.danger);
  });
});

describe('iconSignature', () => {
  it('is the same for two pictures that would look the same', () => {
    expect(iconSignature(MAP_STATUS.ok, false, true)).toBe(iconSignature(MAP_STATUS.ok, true, true));
  });

  it('separates every difference a person can see', () => {
    const keys = new Set([
      iconSignature(MAP_STATUS.ok, false, true),
      iconSignature(MAP_STATUS.warn, false, false),
      iconSignature(MAP_STATUS.danger, false, false),
      iconSignature(MAP_STATUS.danger, true, false),
    ]);
    expect(keys.size).toBe(4);
  });
});
