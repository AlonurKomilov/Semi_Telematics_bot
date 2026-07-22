/**
 * stagedAction contract tests — the banner is mocked at the seam, so
 * these drive the lifecycle hooks directly: commit fires ONLY on expiry
 * (or tab close), cancel really prevents the request, failure offers
 * retry, and nothing ever commits twice.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('./AppBanner', () => ({ showBanner: vi.fn() }));
vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(), error: vi.fn(), dismiss: vi.fn(), custom: vi.fn(),
  }),
}));

import { toast } from 'sonner';
import { showBanner } from './AppBanner';
import { pendingStagedCount, stagedAction, undoableAction } from './stagedAction';
import type { BannerOptions } from './AppBanner';

const bannerCalls = () => (showBanner as ReturnType<typeof vi.fn>).mock.calls;
const lastBanner = (): BannerOptions => {
  const calls = bannerCalls();
  return calls[calls.length - 1][0];
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('stagedAction', () => {
  it('commits ONLY when the countdown expires', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    stagedAction({ label: 'Doing X', commit });
    expect(commit).not.toHaveBeenCalled();          // nothing sent yet
    expect(pendingStagedCount()).toBe(1);

    await lastBanner().onExpire!();
    expect(commit).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalled();
    expect(pendingStagedCount()).toBe(0);
  });

  it('cancel prevents the request entirely', async () => {
    const commit = vi.fn();
    const onCancel = vi.fn();
    stagedAction({ label: 'Doing X', commit, onCancel });

    await lastBanner().actions![0].onClick();       // the Cancel button
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(pendingStagedCount()).toBe(0);
    // A late expiry (banner already dismissed) must not commit.
    await lastBanner().onExpire!();
    expect(commit).not.toHaveBeenCalled();
  });

  it('the ✕ close on a pending action also cancels', async () => {
    const commit = vi.fn();
    const onCancel = vi.fn();
    stagedAction({ label: 'Doing X', commit, onCancel });
    lastBanner().onClose!();
    expect(onCancel).toHaveBeenCalledTimes(1);
    await lastBanner().onExpire!();
    expect(commit).not.toHaveBeenCalled();
  });

  it('tab close flushes the pending commit WITH the keepalive hint', () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    stagedAction({ label: 'Doing X', commit });
    window.dispatchEvent(new Event('pagehide'));
    expect(commit).toHaveBeenCalledTimes(1);
    // The hint is the contract: without keepalive the browser may abort
    // an unload-time fetch and the intent IS silently lost.
    expect(commit).toHaveBeenCalledWith({ keepalive: true });
    expect(pendingStagedCount()).toBe(0);
  });

  it('a failure re-arms the tab-close safety net until Retry or dismiss', async () => {
    const commit = vi.fn().mockRejectedValueOnce(new Error('boom'));
    stagedAction({ label: 'Doing X', commit });
    await lastBanner().onExpire!();          // attempt 1 fails
    // The intent is still live while the failure banner waits — closing
    // the tab now must fire one last keepalive attempt.
    expect(pendingStagedCount()).toBe(1);
    commit.mockResolvedValue(undefined);
    window.dispatchEvent(new Event('pagehide'));
    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit).toHaveBeenLastCalledWith({ keepalive: true });
    expect(pendingStagedCount()).toBe(0);
  });

  it('dismissing the failure banner is the explicit give-up (disarms)', async () => {
    const commit = vi.fn().mockRejectedValueOnce(new Error('boom'));
    stagedAction({ label: 'Doing X', commit });
    await lastBanner().onExpire!();
    lastBanner().onClose!();                 // ✕ on the failure banner
    expect(pendingStagedCount()).toBe(0);
    window.dispatchEvent(new Event('pagehide'));
    expect(commit).toHaveBeenCalledTimes(1); // no ghost retry after give-up
  });

  it('never commits twice (expiry after flush is a no-op)', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    stagedAction({ label: 'Doing X', commit });
    const opts = lastBanner();
    window.dispatchEvent(new Event('pagehide'));
    await opts.onExpire!();
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it('a failed commit shows a danger banner with a working Retry', async () => {
    const commit = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(undefined);
    stagedAction({ label: 'Doing X', commit });
    await lastBanner().onExpire!();

    const err = lastBanner();                       // the failure banner
    expect(err.tone).toBe('danger');
    expect(err.actions![0].label).toBe('Retry');
    await err.actions![0].onClick();
    expect(commit).toHaveBeenCalledTimes(2);
    expect(toast.success).toHaveBeenCalled();
  });
});

describe('undoableAction', () => {
  it('offers a time-boxed Undo that runs the reverse call', async () => {
    const undo = vi.fn().mockResolvedValue(undefined);
    undoableAction({ label: 'Did X', undo });
    const opts = lastBanner();
    expect(opts.countdown).toBe('dismiss');         // display window, not commit
    await opts.actions![0].onClick();
    expect(undo).toHaveBeenCalledTimes(1);
  });
});
