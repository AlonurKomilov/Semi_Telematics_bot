/**
 * Callout — the strip renders labelled answers, separately.
 *
 * A reader arriving at a statement asks the same standing questions:
 * which record, what changed, what does it mean, what does it cost me,
 * what do I do.  A flat paragraph makes them mine it for every one and
 * buries the impact at the end of a sentence; labelled lines let the
 * eye jump to the one they need.
 *
 * WHICH lines appear is the callout's choice, resolved upstream — see
 * useCallout.test.tsx, which proves the selection against the real
 * copy.  This file only proves the strip renders what it is handed and
 * prints nothing for what it is not.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

afterEach(cleanup);

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      'callout.labels.where': 'Where',
      'callout.labels.changed': 'Changed',
      'callout.labels.why': 'Why',
      'callout.labels.affects': 'Affects',
      'callout.labels.do': 'Do',
    }[key] ?? key),
  }),
}));

const resolved = vi.fn();
vi.mock('./useCallout', () => ({ useCallout: () => resolved() }));

import Callout from './Callout';

const FULL = {
  key: 'vehicle.no_engine_data',
  tone: 'warn' as const,
  title: 'No data: Engine',
  short: 'No data',
  lines: [
    { name: 'why' as const, label: 'Why',
      value: 'The device is online but not reading the engine.' },
    { name: 'affects' as const, label: 'Affects',
      value: 'Odometer · Fuel · Engine hours · Mileage totals' },
    { name: 'do' as const, label: 'Do',
      value: 'Have the diagnostic-port connection checked.' },
  ],
  Icon: () => null,
  dismissible: false,
};

describe('Callout strip', () => {
  it('renders each answer under its own label', () => {
    resolved.mockReturnValue(FULL);
    render(<Callout callout={{ key: FULL.key }} />);
    expect(screen.getByText('No data: Engine')).toBeTruthy();
    for (const label of ['Why', 'Affects', 'Do']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText(/Odometer · Fuel/)).toBeTruthy();
  });

  it('states the impact instead of burying it', () => {
    // The cost of this fault is a mileage total that reads zero — the
    // reason the feature exists — so it gets its own line.
    resolved.mockReturnValue(FULL);
    render(<Callout callout={{ key: FULL.key }} />);
    const affects = screen.getByText('Affects').parentElement;
    expect(affects?.textContent).toContain('Mileage totals');
  });

  it('omits a line the callout does not answer', () => {
    resolved.mockReturnValue({
      ...FULL, title: 'Device change', lines: [FULL.lines[0]],
    });
    render(<Callout callout={{ key: 'mileage.device_change' }} />);
    expect(screen.getByText('Why')).toBeTruthy();
    expect(screen.queryByText('Affects')).toBeNull();
    expect(screen.queryByText('Do')).toBeNull();
  });

  it('renders a bare title when it answers nothing else', () => {
    resolved.mockReturnValue({ ...FULL, title: 'Partial', lines: [] });
    render(<Callout callout={{ key: 'mileage.partial' }} />);
    expect(screen.getByText('Partial')).toBeTruthy();
    expect(screen.queryByText('Why')).toBeNull();
  });
});
