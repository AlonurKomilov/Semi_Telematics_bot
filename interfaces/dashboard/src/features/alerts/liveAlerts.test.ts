import { describe, it, expect } from 'vitest';
import { diffNewAlerts, resolvedBanners, type AlertLike } from './liveAlerts';

const a = (id: string, severity: AlertLike['severity'] = 'warning'): AlertLike =>
  ({ id, severity });

describe('diffNewAlerts', () => {
  it('first load establishes a baseline and shows NOTHING', () => {
    const { toShow, seen } = diffNewAlerts(
      [a('1'), a('2', 'critical')], new Set(), 'all', true);
    expect(toShow).toEqual([]);              // no flood on open
    expect(seen).toEqual(new Set(['1', '2']));
  });

  it('after baseline, only genuinely new ids banner', () => {
    let seen = new Set(['1', '2']);
    const r = diffNewAlerts([a('3'), a('2'), a('1')], seen, 'all', false);
    expect(r.toShow.map((x) => x.id)).toEqual(['3']);   // 1,2 already seen
    seen = r.seen;
    // Next poll, nothing new → nothing shows.
    expect(diffNewAlerts([a('3'), a('1')], seen, 'all', false).toShow).toEqual([]);
  });

  it("'critical' level shows only criticals but still marks the rest seen", () => {
    const r = diffNewAlerts(
      [a('10', 'warning'), a('11', 'critical'), a('12', 'info')],
      new Set(), 'critical', false);
    expect(r.toShow.map((x) => x.id)).toEqual(['11']);
    // All three are seen — switching to 'all' later must NOT replay 10/12.
    expect(r.seen).toEqual(new Set(['10', '11', '12']));
    const next = diffNewAlerts(
      [a('10', 'warning'), a('12', 'info')], r.seen, 'all', false);
    expect(next.toShow).toEqual([]);
  });

  it('never re-banners the same id across polls', () => {
    let seen = new Set<string>();
    ({ seen } = diffNewAlerts([a('7', 'critical')], seen, 'all', false));
    const again = diffNewAlerts([a('7', 'critical')], seen, 'all', false);
    expect(again.toShow).toEqual([]);
  });
});


describe('resolvedBanners', () => {
  it('returns banners whose alert is NOT in the authoritative active set', () => {
    const shown = new Map<string, string | number>([
      ['a1', 't1'], ['a2', 't2'], ['a3', 't3'],
    ]);
    const active = new Set(['a1', 'a3']);        // a2 resolved
    expect(resolvedBanners(shown, active)).toEqual([['a2', 't2']]);
  });

  it('keeps all when every shown banner is still active', () => {
    const shown = new Map<string, string | number>([['a1', 1], ['a2', 2]]);
    expect(resolvedBanners(shown, new Set(['a1', 'a2']))).toEqual([]);
  });

  it('returns ALL when the active set is empty (everything resolved)', () => {
    const shown = new Map<string, string | number>([['a1', 1], ['a2', 2]]);
    expect(resolvedBanners(shown, new Set()).map(([id]) => id).sort())
      .toEqual(['a1', 'a2']);
  });

  it('is empty when nothing is shown', () => {
    expect(resolvedBanners(new Map(), new Set(['a1']))).toEqual([]);
  });
});
