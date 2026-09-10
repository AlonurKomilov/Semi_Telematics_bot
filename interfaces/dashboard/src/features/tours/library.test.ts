import { describe, it, expect } from 'vitest';
import { libraryModel, signalPairs } from './library';
import type { TourSpec } from '../../components/tour';

const spec = (key: string, extra: Partial<TourSpec> = {}): TourSpec => ({
  key, feature: 'maintenance', steps: [], relevant: () => true, ...extra,
} as TourSpec);

const rows = (...specs: TourSpec[]) => specs.map((tour) => ({ tour, feature: { path: '/x', labelKey: 'x' } }));

describe('the library counts what the person already does', () => {
  it('a run tour and an adopted one both count as done; skipped and new do not', () => {
    const adopted = spec('b', { signals: ['t:create'], adopted: (ctx) => (ctx.signals?.['t:create']?.grouped ?? 0) > 0 });
    const m = libraryModel(rows(spec('a'), adopted, spec('c'), spec('d')),
      { a: { s: 'done', t: '' }, c: { s: 'skipped', t: '' } },
      { 't:create': { total: 9, solo: 3, grouped: 6 } });
    expect(m.done).toBe(2);
    expect(m.total).toBe(4);
    expect(m.rows.map((r) => `${r.tour.key}:${r.status}`)).toEqual(['d:new', 'c:skipped', 'b:adopted', 'a:done']);
  });

  it('adoption outranks a recorded verdict', () => {
    const t = spec('a', { signals: ['t:create'], adopted: (ctx) => (ctx.signals?.['t:create']?.grouped ?? 0) > 0 });
    const m = libraryModel(rows(t), { a: { s: 'skipped', t: '' } }, { 't:create': { total: 1, solo: 0, grouped: 1 } });
    expect(m.rows[0].status).toBe('adopted');
  });
});

describe('next for you', () => {
  it('is the first tour neither run nor answered, in catalog order', () => {
    const m = libraryModel(rows(spec('a'), spec('b'), spec('c')), { a: { s: 'done', t: '' } }, undefined);
    expect(m.next?.tour.key).toBe('b');
  });

  it('never nags past a skip, and is null when everything is settled', () => {
    expect(libraryModel(rows(spec('a')), { a: { s: 'skipped', t: '' } }, undefined).next).toBeNull();
    expect(libraryModel(rows(spec('a')), { a: { s: 'done', t: '' } }, undefined).next).toBeNull();
    expect(libraryModel([], {}, undefined)).toEqual({ rows: [], done: 0, total: 0, next: null });
  });
});

describe('one request for the page', () => {
  it('collects every declared signal pair once', () => {
    const r = rows(spec('a', { signals: ['x:create', 'y:create'] }), spec('b', { signals: ['x:create'] }), spec('c'));
    expect(signalPairs(r)).toEqual(['x:create', 'y:create']);
  });
});
