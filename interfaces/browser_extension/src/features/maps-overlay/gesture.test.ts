import { describe, it, expect } from 'vitest';

import { FLING_SPEED, beginDrag, dragTransform, endDrag, isMapKey, moveDrag } from './gesture';

describe('following a drag we cannot ask the map about', () => {
  it('translates the layer by exactly the pointer travel', () => {
    let d = beginDrag(100, 100, 0);
    d = moveDrag(d, 130, 90, 16);
    d = moveDrag(d, 160, 80, 32);
    expect(d.dx).toBe(60);
    expect(d.dy).toBe(-20);
    expect(dragTransform(d)).toBe('translate3d(60px, -20px, 0)');
  });

  it('no drag, no transform', () => {
    expect(dragTransform(null)).toBe('');
  });
});

describe('what a release means', () => {
  it('a press with no travel moved nothing — restore at once', () => {
    let d = beginDrag(100, 100, 0);
    d = moveDrag(d, 101, 100, 16);
    expect(endDrag(d)).toEqual({ kind: 'still' });
  });

  it('a slow deliberate pan: keep following, wait for the camera', () => {
    let d = beginDrag(100, 100, 0);
    for (let i = 1; i <= 10; i++) d = moveDrag(d, 100 + i * 2, 100, i * 16);   // 0.125 px/ms
    expect(endDrag(d)).toEqual({ kind: 'panned' });
  });

  it('a flick: the map will keep moving on its own — fade instead of drifting', () => {
    let d = beginDrag(100, 100, 0);
    for (let i = 1; i <= 6; i++) d = moveDrag(d, 100 + i * 20, 100, i * 16);   // 1.25 px/ms
    expect(endDrag(d)).toEqual({ kind: 'flung' });
    expect(Math.hypot(d.vx, d.vy)).toBeGreaterThan(FLING_SPEED);
  });

  it('a pan that slowed to a stop before release is a pan, not a fling', () => {
    // Fast at first, then the hand comes to rest and lifts.  Only the
    // speed AT release should count, which is what smoothing buys.
    let d = beginDrag(100, 100, 0);
    for (let i = 1; i <= 5; i++) d = moveDrag(d, 100 + i * 20, 100, i * 16);
    for (let i = 1; i <= 8; i++) d = moveDrag(d, 200, 100, 80 + i * 16);        // stationary
    expect(endDrag(d)).toEqual({ kind: 'panned' });
  });

  it('a zero-time sample cannot divide by zero', () => {
    const d = moveDrag(beginDrag(0, 0, 10), 5, 5, 10);
    expect(Number.isFinite(d.vx)).toBe(true);
  });
});

describe('keys that move the map', () => {
  it('arrows and zoom keys, nothing else', () => {
    for (const k of ['ArrowUp', 'ArrowLeft', '+', '-', '=']) expect(isMapKey(k)).toBe(true);
    for (const k of ['a', 'Enter', 'Escape', ' ']) expect(isMapKey(k)).toBe(false);
  });
});
