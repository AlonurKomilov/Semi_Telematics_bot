/**
 * CalloutInline — the tooltip is the explanation of LAST resort.
 *
 * A truck whose engine bus is silent renders nine of these on one
 * page (3 in Vehicle Info, 6 in Vehicle Health), under a strip that
 * already carries the full paragraph.  Left alone, each one repeated
 * that paragraph on hover — nine copies of one sentence on one screen.
 *
 * So the tooltip is opt-out, not removed: where a note stands alone
 * (a table cell, a row with no strip above it) it is the only
 * explanation the reader can reach, and dropping it would trade noise
 * for silence.  Both halves are pinned here.
 *
 * The tooltip family is stubbed rather than rendered: this test is
 * about WHICH branch runs, and the real <Tip> drags the app's i18n
 * bootstrap in behind it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

afterEach(cleanup);

vi.mock('../tooltip', () => ({
  Tip: ({ label, children }: { label: string; children: React.ReactNode }) => (
    <span data-testid="tip" data-label={label}>{children}</span>
  ),
}));
vi.mock('./useCallout', () => ({
  useCallout: () => ({
    key: 'vehicle.no_engine_data',
    tone: 'warn' as const,
    title: 'No engine data',
    short: 'No data',
    explanation: 'The device is online but not reading the engine.',
    affects: '',
    act: '',
    Icon: () => null,
    dismissible: false,
  }),
}));

import CalloutInline from './CalloutInline';

const CALLOUT = { key: 'vehicle.no_engine_data' };

describe('CalloutInline', () => {
  it('shows the SHORT form, not the title', () => {
    // The row already says "Fuel" / "Oil Pressure"; repeating the
    // category is redundant and would need new wording for every
    // future kind of gap.
    render(<CalloutInline callout={CALLOUT} explained />);
    expect(screen.getByText('No data')).toBeTruthy();
    expect(screen.queryByText('No engine data')).toBeNull();
  });

  it('carries the explanation when it stands alone', () => {
    render(<CalloutInline callout={CALLOUT} />);
    expect(screen.getByText('No data')).toBeTruthy();
    const tip = screen.getByTestId('tip');
    expect(tip.getAttribute('data-label')).toContain('not reading the engine');
  });

  it('drops the tooltip when the page already explains it', () => {
    render(<CalloutInline callout={CALLOUT} explained />);
    expect(screen.getByText('No data')).toBeTruthy();
    expect(screen.queryByTestId('tip')).toBeNull();
  });

  it('keeps the dash it stands in for', () => {
    render(<CalloutInline callout={CALLOUT} explained />);
    expect(screen.getByText('—')).toBeTruthy();
  });
});
