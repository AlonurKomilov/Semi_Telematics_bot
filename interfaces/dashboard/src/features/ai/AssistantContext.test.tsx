/**
 * The answer that arrives while you are looking somewhere else.
 *
 * A question put to the assistant is answered seconds to minutes later,
 * and the panel can be closed the whole time — the provider's own
 * comment says the stream keeps running after the panel unmounts, and
 * the docked launcher renders a done-dot for exactly that case. Every
 * other lane in the app already sounds when the app answers. This one
 * did not: nothing in `features/ai` played a cue at all.
 *
 * Two properties, and the second is the one that breaks. `setRunState`
 * is called per streaming chunk, and it is the EDGE into `done` that
 * means something — a cue per chunk is a stutter, and a cue inside the
 * state updater fires twice under React's double-invoke.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ReactNode } from 'react';

const { playUiCue } = vi.hoisted(() => ({ playUiCue: vi.fn() }));
vi.mock('../../mods/sound/cue', () => ({ playUiCue }));
import { AssistantProvider, useAssistant } from './AssistantContext';

const wrap = ({ children }: { children: ReactNode }) =>
  <AssistantProvider>{children}</AssistantProvider>;

const run = () => renderHook(() => useAssistant(), { wrapper: wrap });

beforeEach(() => { playUiCue.mockClear(); });

describe('the assistant says when it has answered', () => {
  it('sounds once when a run finishes', () => {
    const { result } = run();
    act(() => { result.current.setRunState('running', 'Reading vehicles'); });
    expect(playUiCue, 'a run STARTING is not an answer').not.toHaveBeenCalled();
    act(() => { result.current.setRunState('done'); });
    expect(playUiCue).toHaveBeenCalledWith('success');
    expect(playUiCue).toHaveBeenCalledTimes(1);
  });

  /**
   * The equality-bailing setter exists because a streaming model calls
   * it per thinking chunk with the same label. `done` arrives the same
   * way, and a cue that follows the CALL rather than the EDGE turns one
   * answer into a burst.
   */
  it('and not again while it stays done', () => {
    const { result } = run();
    act(() => { result.current.setRunState('done'); });
    act(() => { result.current.setRunState('done'); });
    act(() => { result.current.setRunState('done', 'a later label'); });
    expect(playUiCue, 'one answer, announced once').toHaveBeenCalledTimes(1);
  });

  it('but again on the next question', () => {
    const { result } = run();
    act(() => { result.current.setRunState('done'); });
    act(() => { result.current.setRunState('running'); });
    act(() => { result.current.setRunState('done'); });
    expect(playUiCue).toHaveBeenCalledTimes(2);
  });

  /**
   * A run that never reaches `done` is a run that was abandoned or
   * failed, and the lane for a failure is the toast that reports it.
   * Sounding `idle` would announce the person closing their own panel.
   */
  it('and never for a run that just stops', () => {
    const { result } = run();
    act(() => { result.current.setRunState('running'); });
    act(() => { result.current.setRunState('idle'); });
    expect(playUiCue).not.toHaveBeenCalled();
  });

});
