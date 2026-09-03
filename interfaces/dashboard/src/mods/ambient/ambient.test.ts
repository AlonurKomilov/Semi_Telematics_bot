/**
 * Ambient mode: the resolver, the growth, and the one thing that must
 * NOT grow.
 *
 * The registry is empty today, so the resolver is tested against
 * synthetic entries — a resolver checked only against an empty map is a
 * resolver checked against nothing, and the whole reason the map exists
 * is that somebody will put something in it later.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  AMBIENT_SCALE, AMBIENT_AFTER_MS, AMBIENT_VIEWS, PRESENCE_EVENTS,
  resolveAmbientView, type AmbientView,
} from './ambient';
import { applySize } from '../context';
import { SIZE_DEFAULT } from '../../preferences';

describe('the registry ships empty, and the resolver still works', () => {
  it('is empty — a half-built view would make the mode look page-specific', () => {
    expect(Object.keys(AMBIENT_VIEWS)).toEqual([]);
  });

  it('falls back to "grow the page you already have"', () => {
    expect(resolveAmbientView('/loads')).toBeNull();
    expect(resolveAmbientView('/anything/at/all')).toBeNull();
  });

  const A: AmbientView = () => null;
  const B: AmbientView = () => null;

  it('matches a prefix, and a child of it', () => {
    const views = { '/loads': A };
    expect(resolveAmbientView('/loads', views)).toBe(A);
    expect(resolveAmbientView('/loads/42', views)).toBe(A);
  });

  it('does not match a sibling that merely starts with the same letters', () => {
    // `/loadsheets` is not under `/loads`, and a naive startsWith says
    // it is.
    expect(resolveAmbientView('/loadsheets', { '/loads': A })).toBeNull();
  });

  it('the longest registration wins, whatever order the object is in', () => {
    const wide = { '/loads': A, '/loads/42': B };
    const narrow = { '/loads/42': B, '/loads': A };
    expect(resolveAmbientView('/loads/42', wide)).toBe(B);
    expect(resolveAmbientView('/loads/42', narrow)).toBe(B);
  });

  it('watches for presence in more than one way', () => {
    // A mode that only noticed the mouse would settle on somebody
    // reading with the keyboard.
    expect(PRESENCE_EVENTS).toContain('pointermove');
    expect(PRESENCE_EVENTS).toContain('keydown');
    expect(PRESENCE_EVENTS.length).toBeGreaterThan(3);
  });

  it('settles slowly enough to read a long report', () => {
    expect(AMBIENT_AFTER_MS).toBeGreaterThanOrEqual(60_000);
  });
});

describe('what grows, and what deliberately does not', () => {
  const read = (n: string) => document.documentElement.style.getPropertyValue(n);

  beforeEach(() => { document.documentElement.removeAttribute('style'); });

  it('multiplies the size the person already chose', () => {
    applySize({ ...SIZE_DEFAULT, global: 1.2 }, AMBIENT_SCALE);
    // Composes rather than replaces — somebody on the `wall` mod at 145%
    // is exactly the person who wants both.
    expect(Number(read('--size-text'))).toBeCloseTo(1.2 * AMBIENT_SCALE, 6);
    expect(Number(read('--size-panel'))).toBeCloseTo(1.2 * AMBIENT_SCALE, 6);
  });

  it('leaves overlays at their true size, so an alert does not grow with the page', () => {
    applySize(SIZE_DEFAULT, AMBIENT_SCALE);
    // Regions multiply the global, so the overlay region has to divide
    // the ambient factor back out. The product is what a viewer sees.
    const net = Number(read('--size-panel')) * Number(read('--size-region-overlays'));
    expect(net).toBeCloseTo(1, 6);
  });

  it('and still honours an overlay size the person set themselves', () => {
    applySize({ ...SIZE_DEFAULT, regions: { overlays: 1.5 } }, AMBIENT_SCALE);
    const net = Number(read('--size-panel')) * Number(read('--size-region-overlays'));
    expect(net, 'ambient overwrote a choice instead of composing with it').toBeCloseTo(1.5, 6);
  });

  it('changes nothing at all when ambient is off', () => {
    applySize({ ...SIZE_DEFAULT, global: 1.2 });
    expect(Number(read('--size-text'))).toBeCloseTo(1.2, 6);
    expect(read('--size-region-overlays'), 'an unset region grew a value').toBe('');
  });
});
