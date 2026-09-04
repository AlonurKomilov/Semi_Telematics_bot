/**
 * The lane speaks once per notification — not once per render of one.
 *
 * `showBanner(opts, existingId)` re-renders a banner already on screen
 * so it can learn something after it was shown: LiveAlertWatcher uses it
 * to annotate an alert a colleague has just claimed. That path is not a
 * new notification, and sounding there would announce the same alert
 * again every time anybody touched it — once per claim, per alert, for
 * as long as the banner lives.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { playBannerCue } = vi.hoisted(() => ({ playBannerCue: vi.fn() }));
vi.mock('../../mods/sound/cue', () => ({
  playBannerCue,
  playUiCue: () => {},
  armIfWanted: () => {},
  installKeySound: () => {},
}));

const { toast } = vi.hoisted(() => ({
  toast: Object.assign(vi.fn(), {
    custom: vi.fn(() => 'banner-1'),
    success: vi.fn(), error: vi.fn(), dismiss: vi.fn(), loading: vi.fn(),
  }),
}));
vi.mock('sonner', () => ({ toast, Toaster: () => null }));

import { showBanner } from './AppBanner';

beforeEach(() => { playBannerCue.mockClear(); toast.custom.mockClear(); });

describe('a new notification sounds', () => {
  it('and its tone is what it sounds', () => {
    showBanner({ tone: 'danger', title: 'Engine fault — 253' });
    expect(playBannerCue).toHaveBeenCalledWith('danger');
    // …and the banner is still shown, or a cue replaced a control.
    expect(toast.custom, 'the banner was not raised').toHaveBeenCalled();
  });

  it('a caller that sounds its own cue can opt the lane out', () => {
    showBanner({ tone: 'ok', title: 'Acknowledged 12 alerts', cue: false });
    expect(playBannerCue, 'the lane spoke over a caller that sounds its own')
      .not.toHaveBeenCalled();
    expect(toast.custom).toHaveBeenCalled();
  });

  it('a caller may name the cue instead of leaving it to the tone', () => {
    showBanner({ tone: 'ok', title: 'Reset', cue: 'undo' });
    expect(playBannerCue).toHaveBeenCalledWith('undo');
  });
});

describe('re-rendering one is not a new notification', () => {
  it('stays silent when replacing a banner already on screen', () => {
    showBanner({ tone: 'danger', title: 'Engine fault — 253' }, 'banner-1');
    expect(
      playBannerCue,
      'annotating a live banner announced its alert again — once per claim, '
        + 'per alert, for as long as the banner lives',
    ).not.toHaveBeenCalled();
    // The re-render itself must still happen.
    expect(toast.custom, 'the banner was not re-rendered').toHaveBeenCalled();
  });

  it('the first showing of the same banner DID sound, so the skip is the difference', () => {
    showBanner({ tone: 'danger', title: 'Engine fault — 253' });
    expect(playBannerCue).toHaveBeenCalledTimes(1);
    showBanner({ tone: 'danger', title: 'Engine fault — 253 · claimed by Ada' }, 'banner-1');
    expect(playBannerCue).toHaveBeenCalledTimes(1);
  });
});
