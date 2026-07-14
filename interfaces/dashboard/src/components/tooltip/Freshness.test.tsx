/**
 * Freshness — per-metric staleness cue thresholds.
 *
 * fresh (<1h): value only (tooltip on hover, no visible cue)
 * stale (≥1h): warn dot
 * very stale (≥24h): dot + inline short age
 * no/invalid timestamp: children untouched
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Freshness } from './Freshness';

// The component only needs a timezone string and a ticking clock —
// stub the hooks so the test never touches auth context.  The clock is
// FAKED system-wide (formatRelative reads Date.now() directly), so both
// the hook and the formatter see the same frozen instant.
vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));
vi.mock('../../hooks/useNow', () => ({ useNow: () => new Date() }));

beforeEach(() => {
  vi.useFakeTimers({ now: Date.parse('2026-07-14T12:00:00Z'), toFake: ['Date'] });
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const iso = (minutesAgo: number) =>
  new Date(Date.parse('2026-07-14T12:00:00Z') - minutesAgo * 60_000).toISOString();

describe('Freshness thresholds', () => {
  it('renders children untouched without a timestamp', () => {
    render(<Freshness ts={null}>751,599 mi</Freshness>);
    expect(screen.getByText('751,599 mi')).toBeTruthy();
    expect(document.querySelector('[data-slot=tooltip-trigger]')).toBeNull();
  });

  it('fresh reading: tooltip wrapper, no visible cue', () => {
    render(<Freshness ts={iso(5)}>88%</Freshness>);
    expect(screen.getByText('88%')).toBeTruthy();
    expect(document.querySelector('[data-slot=tooltip-trigger]')).toBeTruthy();
    expect(document.querySelector('[aria-label^="updated"]')).toBeNull();
  });

  it('stale reading (≥1h): warn dot appears', () => {
    render(<Freshness ts={iso(90)}>88%</Freshness>);
    expect(document.querySelector('[aria-label="updated 1h ago"]')).toBeTruthy();
    // no inline age yet at the 1h tier
    expect(screen.queryByText(/·/)).toBeNull();
  });

  it('very stale reading (≥24h): dot + inline short age', () => {
    render(<Freshness ts={iso(3 * 24 * 60)}>88%</Freshness>);
    expect(document.querySelector('[aria-label="updated 3d ago"]')).toBeTruthy();
    expect(screen.getByText(/· 3d ago/)).toBeTruthy();
  });

  it('months-old reading: calendar date, no year within the current year', () => {
    render(<Freshness ts={iso(60 * 24 * 60)}>88%</Freshness>);
    // 60 days before Jul 14, 2026 = May 15, 2026 (same year → no year)
    expect(screen.getByText(/· May 15$/)).toBeTruthy();
  });

  it('previous-year reading: calendar date with year', () => {
    render(<Freshness ts={'2025-06-26T20:03:22Z'}>Anaheim</Freshness>);
    expect(screen.getByText(/· Jun 26, 2025/)).toBeTruthy();
  });
});
