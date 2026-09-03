import { describe, it, expect } from 'vitest';
import { FALLBACK, FALLBACK_AFTER_ERRORS, TILES, shouldFallBack } from './tiles';

describe('tile source fallback — a grey map is a failed source, not a slow one', () => {
  it('a few errors while tiles keep arriving is just the internet', () => {
    expect(shouldFallBack(2, 10)).toBe(false);
    expect(shouldFallBack(FALLBACK_AFTER_ERRORS, 12)).toBe(false);
  });
  it('errors with nothing (or less) arriving means this person cannot reach OpenStreetMap', () => {
    expect(shouldFallBack(FALLBACK_AFTER_ERRORS, 0)).toBe(true);
    expect(shouldFallBack(6, 3)).toBe(true);
  });
  it('one or two errors never trip it — a single missing tile is normal', () => {
    expect(shouldFallBack(1, 0)).toBe(false);
    expect(shouldFallBack(FALLBACK_AFTER_ERRORS - 1, 0)).toBe(false);
  });
  it('the fallback is keyless, on the host the satellite layer already trusts', () => {
    expect(FALLBACK.url).toContain('server.arcgisonline.com');
    expect(TILES.satellite.url).toContain('server.arcgisonline.com');
    expect(FALLBACK.url).not.toContain('key=');
  });
});
