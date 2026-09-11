/**
 * The overlay is the one member of the failure family that covers a
 * LIVE session, so its two ways of being wrong both cost the user
 * something real:
 *
 *   · announcing when nothing is wrong puts an outage card over a
 *     perfectly healthy app — and React Query aborts requests
 *     constantly, on every unmount and every refetch
 *   · never giving up leaves "a quick update… under a minute" on screen
 *     through a thirty-minute outage, which is a lie the person acts on
 *
 * Both are asserted here rather than trusted to the comments that
 * explain them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import MaintenanceOverlay from './MaintenanceOverlay';

function announce(reason?: 'updating' | 'unreachable') {
  act(() => {
    window.dispatchEvent(
      reason
        ? new CustomEvent('4truck:maintenance', { detail: { reason } })
        : new Event('4truck:maintenance'),
    );
  });
}

/** Let the poller run for `ms` of fake time, with fetch always failing. */
async function waitDown(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe('MaintenanceOverlay', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('shows nothing until something announces', () => {
    render(<MaintenanceOverlay />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('speaks as ABC Checker, like the other three', () => {
    render(<MaintenanceOverlay />);
    announce('updating');
    expect(screen.getByText('ABC Checker')).toBeTruthy();
  });

  it('calls a restart an update and a dead connection a dead connection', () => {
    const { unmount } = render(<MaintenanceOverlay />);
    announce('updating');
    expect(screen.getByText('Updating the platform')).toBeTruthy();
    unmount();

    render(<MaintenanceOverlay />);
    announce('unreachable');
    expect(screen.getByText('Connection lost')).toBeTruthy();
    // ...and never calls a lost connection a deploy
    expect(screen.queryByText('Updating the platform')).toBeNull();
  });

  it('treats a bare event as a restart, the way it always did', () => {
    render(<MaintenanceOverlay />);
    announce();
    expect(screen.getByText('Updating the platform')).toBeTruthy();
  });

  it('stops claiming it is a quick update once it plainly is not', async () => {
    render(<MaintenanceOverlay />);
    announce('updating');
    expect(screen.getByText('Updating the platform')).toBeTruthy();

    await waitDown(30_000);
    expect(screen.getByText('Updating the platform'), 'half a minute is still a deploy').toBeTruthy();

    await waitDown(70_000);   // past the 90s threshold
    expect(screen.getByText('This update is taking longer than usual')).toBeTruthy();
    expect(screen.queryByText('Updating the platform')).toBeNull();
  });

  it('offers the full page only once it has escalated, and in a new tab', async () => {
    render(<MaintenanceOverlay />);
    announce('unreachable');
    expect(screen.queryByText('See what to try')).toBeNull();

    await waitDown(100_000);
    const link = screen.getByText('See what to try').closest('a')!;
    // offline.html is precached by the service worker, so it opens with
    // the network down — which is exactly when it is offered.
    expect(link.getAttribute('href')).toBe('/offline.html');
    // The session under the overlay is the thing being protected.
    expect(link.getAttribute('target')).toBe('_blank');
  });
});
