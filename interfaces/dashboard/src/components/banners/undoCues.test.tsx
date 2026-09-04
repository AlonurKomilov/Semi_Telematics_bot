/**
 * The four cues that were declared, packed, validated and never played.
 *
 * `engine.test.ts` already asserts every pack DEFINES every cue, which
 * is why the gap survived so long: the suite was green on a feature no
 * user could hear. What was missing is a guard on the other end — that
 * something CALLS them.
 *
 * Both undo wrappers are covered here, in one file, because they are one
 * behaviour with two renderings: `undoableAction` shows a banner and
 * `undoableToast` shows a toast, and a person should not be able to tell
 * from the sound which one they got.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { playUiCue, playBannerCue } = vi.hoisted(() => ({
  playUiCue: vi.fn(), playBannerCue: vi.fn(),
}));
vi.mock('../../mods/sound/cue', () => ({
  playUiCue, playBannerCue, armIfWanted: () => {}, installKeySound: () => {},
}));

const { toast } = vi.hoisted(() => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(), error: vi.fn(), custom: vi.fn(() => 'id'),
    dismiss: vi.fn(), loading: vi.fn(() => 'pending'),
  }),
}));
vi.mock('sonner', () => ({ toast, Toaster: () => null }));

const { apiJSON } = vi.hoisted(() => ({ apiJSON: vi.fn() }));
vi.mock('../../api/client', async (orig) => ({
  ...(await orig<Record<string, unknown>>()), apiJSON,
}));

import { undoableAction } from './stagedAction';
import { undoableToast } from '../../lib/undoable';

/** The Undo handler the banner was built with. */
const undoHandler = () => {
  // The hoisted spies infer an empty tuple for their args, so the
  // recorded calls need widening before they can be read back.
  const calls = toast.custom.mock.calls as unknown as unknown[][];
  const render = calls[calls.length - 1]?.[0] as (id: string) => {
    props: { opts: { actions: { label: string; onClick: () => Promise<void> }[] } };
  };
  const el = render('banner-1');
  return el.props.opts.actions.find((a) => a.label === 'Undo')!.onClick;
};

beforeEach(() => {
  playUiCue.mockClear(); playBannerCue.mockClear();
  toast.custom.mockClear(); toast.success.mockClear();
  toast.error.mockClear(); toast.loading.mockClear();
  apiJSON.mockReset();
});

describe('the banner wrapper', () => {
  it('sounds the undo cue when the window opens — and ONLY that one', () => {
    undoableAction({ label: 'Acknowledged 12 alerts', undo: async () => {} });
    expect(playUiCue).toHaveBeenCalledWith('undo');
    // The banner lane sounds every notification by tone. This one is an
    // `ok` banner, so the lane would say "success" over the "undo" this
    // function already said — two sounds for one event. `cue: false` is
    // what stops it, and this is the assertion that keeps it there.
    expect(playBannerCue, 'the lane spoke over the undo cue').not.toHaveBeenCalled();
    // The banner itself must still be raised — a cue that replaced the
    // control instead of accompanying it would pass a naive assertion.
    expect(toast.custom, 'the banner stopped being shown').toHaveBeenCalled();
  });

  it('sounds success when the undo lands', async () => {
    const undo = vi.fn(async () => {});
    undoableAction({ label: 'Reset', undo });
    playUiCue.mockClear();
    await undoHandler()();
    expect(undo).toHaveBeenCalled();
    expect(playUiCue).toHaveBeenCalledWith('success');
    expect(playUiCue).not.toHaveBeenCalledWith('error');
  });

  it('sounds error when it does not', async () => {
    undoableAction({ label: 'Reset', undo: async () => { throw new Error('nope'); } });
    playUiCue.mockClear();
    await undoHandler()();
    expect(playUiCue).toHaveBeenCalledWith('error');
    expect(playUiCue).not.toHaveBeenCalledWith('success');
  });
});

describe('the toast wrapper', () => {
  it('sounds the undo cue when there is something to undo', () => {
    undoableToast({ message: '3 records deleted', groupId: 'g1' });
    expect(playUiCue).toHaveBeenCalledWith('undo');
  });

  it('stays silent when there is not', () => {
    // The no-group path shows a plain toast with no Undo button. A cue
    // that says "you can take this back" over it is a small lie, and
    // small lies are how people learn to stop trusting the sound.
    undoableToast({ message: 'Saved' });
    expect(playUiCue, 'promised an undo that does not exist').not.toHaveBeenCalled();
    expect(toast.success, 'the toast itself stopped being shown').toHaveBeenCalled();
  });

  it('sounds success when the restore comes back', async () => {
    apiJSON.mockResolvedValue({ restored: 3, conflicts: [] });
    undoableToast({ message: 'x', groupId: 'g1' });
    playUiCue.mockClear();
    const calls = toast.success.mock.calls as unknown as unknown[][];
    const action = calls[calls.length - 1]?.[1] as
      { action: { onClick: () => void } };
    action.action.onClick();
    await vi.waitFor(() => expect(playUiCue).toHaveBeenCalledWith('success'));
  });

  it('sounds error when it does not', async () => {
    apiJSON.mockRejectedValue(new Error('gone'));
    undoableToast({ message: 'x', groupId: 'g1' });
    playUiCue.mockClear();
    const calls = toast.success.mock.calls as unknown as unknown[][];
    const action = calls[calls.length - 1]?.[1] as
      { action: { onClick: () => void } };
    action.action.onClick();
    await vi.waitFor(() => expect(playUiCue).toHaveBeenCalledWith('error'));
  });
});
