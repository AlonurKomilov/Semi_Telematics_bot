/**
 * Moving between pages — and the four route changes that are not moving.
 *
 * Nine of the eleven act cues are classified from a click, which is what
 * makes them impossible for a feature to get wrong. This one is RAISED,
 * so every way it can fire wrongly has to be written down: a cue with no
 * act behind it is indistinguishable from a fault, and on a route change
 * the person is usually looking at something else when it happens.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const { playActCue } = vi.hoisted(() => ({ playActCue: vi.fn() }));
vi.mock('./cue', () => ({ playActCue }));

let where = { pathname: '/loads', search: '' };
let how = 'POP';
vi.mock('react-router-dom', () => ({
  useLocation: () => where,
  useNavigationType: () => how,
}));

import { usePageCue } from './route';

/** One mount, then a sequence of route changes through the same hook. */
function walk(steps: ReadonlyArray<{ to: string; how?: string; search?: string }>) {
  const { rerender } = renderHook(() => usePageCue());
  for (const s of steps) {
    where = { pathname: s.to, search: s.search ?? '' };
    how = s.how ?? 'PUSH';
    rerender();
  }
}

beforeEach(() => {
  playActCue.mockClear();
  where = { pathname: '/loads', search: '' };
  how = 'POP';
});

describe('a page opening and a page closing', () => {
  it('forward is opening', () => {
    walk([{ to: '/vehicles', how: 'PUSH' }]);
    expect(playActCue).toHaveBeenCalledWith('page_open');
  });

  it('and Back is closing', () => {
    walk([{ to: '/vehicles', how: 'PUSH' }, { to: '/loads', how: 'POP' }]);
    expect(playActCue).toHaveBeenLastCalledWith('page_close');
  });
});

describe('what is not a person moving', () => {
  /**
   * The one that breaks a naive version on the very first screen:
   * `useNavigationType()` reports POP on a cold load, because the
   * browser is restoring a history entry. Without the guard every page
   * refresh announces itself as a page CLOSING — backwards, and
   * unprompted, before anybody has touched anything.
   */
  it('arriving is not moving', () => {
    renderHook(() => usePageCue());
    expect(playActCue, 'the app announced its own startup').not.toHaveBeenCalled();
  });

  /**
   * `router.tsx` carries redirects. A person bounced off a page they
   * may not see did not go anywhere, and sounding it would be the app
   * announcing its own decision as if it were theirs.
   */
  it('a redirect is not moving', () => {
    walk([{ to: '/overview', how: 'REPLACE' }]);
    expect(playActCue).not.toHaveBeenCalled();
  });

  it('and a redirect does not arm the next one either', () => {
    walk([{ to: '/overview', how: 'REPLACE' }, { to: '/parts', how: 'PUSH' }]);
    expect(playActCue).toHaveBeenCalledTimes(1);
    expect(playActCue).toHaveBeenCalledWith('page_open');
  });

  /**
   * Nearly every table page in this product writes its segment into the
   * query string. Keying on the whole location would make sorting a
   * grid sound like leaving the page.
   */
  it('a query string is not moving', () => {
    walk([{ to: '/loads', how: 'PUSH', search: '?tab=open' }]);
    expect(playActCue, 'a grid writing its own state sounded like a navigation')
      .not.toHaveBeenCalled();
  });

  it('and a nav item pointing where you already are is not moving', () => {
    walk([{ to: '/loads', how: 'PUSH' }]);
    expect(playActCue).not.toHaveBeenCalled();
  });
});

describe('the scan can fail', () => {
  it('two real moves sound twice — the positive control', () => {
    // Without this, every assertion above is satisfied by a hook that
    // never plays anything at all.
    walk([{ to: '/a', how: 'PUSH' }, { to: '/b', how: 'PUSH' }]);
    expect(playActCue).toHaveBeenCalledTimes(2);
  });
});
