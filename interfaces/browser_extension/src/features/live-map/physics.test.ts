import { describe, it, expect } from 'vitest';
import { applyFix, bearingBetween, distMetres, positionAt, shortestAngleDiff, statusColor, vehicleStatus, MAP_STATUS } from './physics';

describe('motion model — the reason the map glides instead of teleporting', () => {
  it('a first fix places the truck and starts no tween', () => {
    const { phys, started } = applyFix(undefined, 41, -87, 55, 90, 1000);
    expect(phys.lat).toBe(41);
    expect(phys.duration).toBe(0);
    expect(started).toBe(true);
  });
  it('a second fix while moving tweens from the drawn position to the new fix', () => {
    const a = applyFix(undefined, 41, -87, 55, 90, 1000).phys;
    const { phys } = applyFix(a, 41.01, -87, 55, null, 6000);
    expect([phys.fromLat, phys.toLat]).toEqual([41, 41.01]);
    expect(phys.duration).toBe(5000);                    // elapsed since last fix
    expect(positionAt(phys, 8500).lat).toBeCloseTo(41.005, 5); // halfway
    expect(positionAt(phys, 11000).done).toBe(true);
  });
  it('the tween is clamped so a long gap does not crawl for a minute', () => {
    const a = applyFix(undefined, 41, -87, 55, 0, 0).phys;
    expect(applyFix(a, 41.1, -87, 55, 0, 60_000).phys.duration).toBe(8000);
    expect(applyFix(a, 41.1, -87, 55, 0, 100).phys.duration).toBe(1500);
  });
  it('heading follows the track when it moved, the sensor when it barely did', () => {
    const a = applyFix(undefined, 41, -87, 55, 0, 0).phys;
    expect(applyFix(a, 41.01, -87, 55, 270, 5000).phys.targetHeading).toBeCloseTo(0, 0); // due north track
    expect(applyFix(a, 41.000001, -87, 55, 270, 5000).phys.targetHeading).toBe(270);
  });
  it('stopping snaps to the fix and reports the transition once', () => {
    const a = applyFix(undefined, 41, -87, 55, 0, 0).phys;
    const b = applyFix(a, 41.01, -87, 0, null, 5000);
    expect(b.stopped).toBe(true);
    expect(b.phys.lat).toBe(41.01);
    expect(applyFix(b.phys, 41.01, -87, 0, null, 10000).stopped).toBe(false);
  });
  it('angles wrap the short way round', () => {
    expect(shortestAngleDiff(350, 10)).toBe(20);
    expect(shortestAngleDiff(10, 350)).toBe(-20);
  });
  it('geometry helpers are sane', () => {
    expect(bearingBetween(0, 0, 1, 0)).toBeCloseTo(0, 5);
    expect(bearingBetween(0, 0, 0, 1)).toBeCloseTo(90, 5);
    expect(distMetres(0, 0, 0.001, 0)).toBeCloseTo(111.1, 0);
  });
  it('status colours match the dashboard exactly', () => {
    expect(statusColor('moving')).toBe(MAP_STATUS.ok);
    expect(statusColor('idle')).toBe(MAP_STATUS.warn);
    expect(statusColor('stopped')).toBe(MAP_STATUS.danger);
    expect(vehicleStatus({ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] },
      properties: { name: 'x', speed_mph: 0, engine_state: 'Idle' } })).toBe('idle');
  });
});
