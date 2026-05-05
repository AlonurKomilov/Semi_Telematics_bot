import { describe, it, expect, beforeEach } from 'vitest';
import {
  fmtSpeed,
  fmtDistance,
  getUnitSystem,
  setUnitSystem,
  speedUnit,
} from './units';

describe('utils/units', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to imperial', () => {
    expect(getUnitSystem()).toBe('imperial');
    expect(speedUnit()).toBe('mph');
  });

  it('persists user preference and reads it back', () => {
    setUnitSystem('metric');
    expect(getUnitSystem()).toBe('metric');
    expect(speedUnit()).toBe('km/h');
  });

  it('formats speed in mph (imperial)', () => {
    expect(fmtSpeed(60, 'imperial')).toBe('60 mph');
    expect(fmtSpeed(null, 'imperial')).toBe('—');
  });

  it('converts speed to km/h (metric)', () => {
    // 60 mph ≈ 96.56 km/h → rounds to 97
    expect(fmtSpeed(60, 'metric')).toBe('97 km/h');
  });

  it('formats distance in miles vs km', () => {
    expect(fmtDistance(10, 'imperial')).toBe('10.0 mi');
    // 10 miles ≈ 16.09 km
    expect(fmtDistance(10, 'metric')).toBe('16.1 km');
    expect(fmtDistance(null, 'imperial')).toBe('—');
  });
});
