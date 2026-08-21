/**
 * The mount trigger's contract: fires when the element nears the
 * viewport, fires ONCE (never un-fires), and degrades to the timer
 * stagger where IntersectionObserver doesn't exist.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useNearViewport } from './useNearViewport';

type IOCallback = (entries: Array<{ isIntersecting: boolean }>) => void;

describe('useNearViewport', () => {
  let callbacks: IOCallback[];
  let disconnects: number;

  beforeEach(() => {
    callbacks = [];
    disconnects = 0;
    vi.stubGlobal('IntersectionObserver', class {
      cb: IOCallback;
      constructor(cb: IOCallback) { this.cb = cb; callbacks.push(cb); }
      observe() {}
      disconnect() { disconnects += 1; }
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('starts not-near, fires on intersection, and never un-fires', () => {
    const { result } = renderHook(() => useNearViewport(0));
    expect(result.current.near).toBe(false);

    act(() => result.current.ref(document.createElement('div')));
    expect(callbacks).toHaveLength(1);

    act(() => callbacks[0]([{ isIntersecting: true }]));
    expect(result.current.near).toBe(true);

    // A later non-intersecting report must not resurrect the gate.
    act(() => callbacks[0]([{ isIntersecting: false }]));
    expect(result.current.near).toBe(true);
  });

  it('ignores non-intersecting reports while waiting', () => {
    const { result } = renderHook(() => useNearViewport(3));
    act(() => result.current.ref(document.createElement('div')));
    act(() => callbacks[0]([{ isIntersecting: false }]));
    expect(result.current.near).toBe(false);
  });

  it('disconnects the observer when the element detaches', () => {
    const { result } = renderHook(() => useNearViewport(0));
    act(() => result.current.ref(document.createElement('div')));
    act(() => result.current.ref(null));
    expect(disconnects).toBeGreaterThan(0);
    expect(result.current.near).toBe(false);
  });

  it('falls back to the timer stagger without IntersectionObserver', () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('IntersectionObserver', undefined);
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useNearViewport(6));
      act(() => result.current.ref(document.createElement('div')));
      expect(result.current.near).toBe(false);
      act(() => { vi.advanceTimersByTime(3 * 16); });
      expect(result.current.near).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
