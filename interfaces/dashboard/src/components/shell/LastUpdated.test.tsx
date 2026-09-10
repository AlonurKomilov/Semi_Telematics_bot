/**
 * The chip whose whole job is stating data age must not state it wrongly.
 * It kept reading "Updated 4m ago" — then 8m, then 15m — while every
 * refresh was failing.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));

import LastUpdated from './LastUpdated';

describe('LastUpdated', () => {
  it('states the age when the last load succeeded', () => {
    render(<LastUpdated fetchedAt={Date.now() - 4 * 60_000} onRefresh={() => {}} />);
    expect(document.body.textContent).toContain('Updated 4m ago');
  });

  it('names the failure and dates the last GOOD load when a refresh failed', () => {
    render(<LastUpdated fetchedAt={Date.now() - 4 * 60_000} error={new Error('502')} onRefresh={() => {}} />);
    const t = document.body.textContent ?? '';
    expect(t).toContain('Refresh failed');
    expect(t).toContain('last good 4m ago');
    expect(t).not.toContain('Updated 4m ago');
  });

  it('does not claim failure mid-refresh', () => {
    render(<LastUpdated fetchedAt={Date.now()} error={new Error('x')} isFetching onRefresh={() => {}} />);
    expect(document.body.textContent).not.toContain('Refresh failed');
  });
});
