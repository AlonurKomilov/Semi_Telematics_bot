import { describe, it, expect } from 'vitest';
import type { AnyColumn } from '../../../types';
import {
  rowPassesColFilter, rowMatchesSearch, tabMatch, tabIsEmpty, type SavedTab,
} from './savedTabs';

const col = (over: Partial<AnyColumn>): AnyColumn =>
  ({ key: 'k', label: 'K', ...over } as AnyColumn);

type Row = Record<string, unknown>;

describe('rowPassesColFilter — select mode', () => {
  const c = col({ key: 'category' });
  it('keeps a row whose value is in the selected set', () => {
    expect(rowPassesColFilter({ category: 'Camera' }, c, ['Camera'])).toBe(true);
    expect(rowPassesColFilter({ category: 'Tablet' }, c, ['Camera', 'ELD'])).toBe(false);
  });
  it('an empty / missing filter keeps every row', () => {
    expect(rowPassesColFilter({ category: 'Tablet' }, c, [])).toBe(true);
    expect(rowPassesColFilter({ category: 'Tablet' }, c, undefined)).toBe(true);
  });
  it('uses filterValue when the raw value differs from the match code', () => {
    const cv = col({ key: 'task_type', filterValue: (r) => String((r as Row).task_type ?? '') });
    expect(rowPassesColFilter({ task_type: 'oil' }, cv, ['oil'])).toBe(true);
    expect(rowPassesColFilter({ task_type: 'oil' }, cv, ['dpf'])).toBe(false);
  });
});

describe('rowPassesColFilter — range mode', () => {
  const c = col({ key: 'miles', filterMode: 'range' });
  it('respects inclusive [min, max] bounds', () => {
    expect(rowPassesColFilter({ miles: 500 }, c, [100, 1000])).toBe(true);
    expect(rowPassesColFilter({ miles: 50 }, c, [100, 1000])).toBe(false);
    expect(rowPassesColFilter({ miles: 5000 }, c, [100, 1000])).toBe(false);
  });
  it('one-sided ranges and a non-numeric value', () => {
    expect(rowPassesColFilter({ miles: 5000 }, c, [1000, null])).toBe(true);
    expect(rowPassesColFilter({ miles: 'x' }, c, [100, null])).toBe(false);
    expect(rowPassesColFilter({ miles: 5 }, c, [null, null])).toBe(true); // empty range = keep
  });
});

describe('rowPassesColFilter — date-range mode', () => {
  const c = col({ key: 'date', filterMode: 'date-range' });
  it('keeps rows within the inclusive day range (To = end of day)', () => {
    expect(rowPassesColFilter({ date: '2026-07-20' }, c, ['2026-07-01', '2026-07-31'])).toBe(true);
    expect(rowPassesColFilter({ date: '2026-08-01' }, c, ['2026-07-01', '2026-07-31'])).toBe(false);
    // single-day filter keeps the whole day
    expect(rowPassesColFilter({ date: '2026-07-20T18:00:00Z' }, c, ['2026-07-20', '2026-07-20'])).toBe(true);
  });
});

describe('rowMatchesSearch', () => {
  const keys = ['name', 'ref'];
  it('matches when any search key contains the needle (case-insensitive)', () => {
    expect(rowMatchesSearch({ name: 'Truck 233', ref: 'ABC' }, keys, '233')).toBe(true);
    expect(rowMatchesSearch({ name: 'Truck 233', ref: 'ABC' }, keys, 'abc')).toBe(true);
    expect(rowMatchesSearch({ name: 'Truck 233', ref: 'ABC' }, keys, 'zzz')).toBe(false);
  });
  it('an empty needle matches everything', () => {
    expect(rowMatchesSearch({ name: 'x' }, keys, '')).toBe(true);
  });

  // The Carrier Directory bug: a page declared 2 search keys while
  // rendering 74 field columns, so a value the operator could read on
  // screen reported "No match".
  it('matches a COLUMN the page never listed in searchKeys', () => {
    const cols = [col({ key: 'f:pay:solo_rate', label: 'Solo Pay Rate' })];
    const row = { name: 'TESTS CARRIER', 'f:pay:solo_rate': '60' };
    expect(rowMatchesSearch(row, keys, '60', cols)).toBe(true);
    // …and without the columns it still misses, which is the old bug.
    expect(rowMatchesSearch(row, keys, '60')).toBe(false);
  });

  it('matches the word a badge column DISPLAYS, not the code behind it', () => {
    const cols = [col({
      key: 'sev',
      filterValue: (r) => String((r as Row).sev ?? ''),
      filterLabel: (r) => ((r as Row).sev === 'crit' ? 'Critical' : 'Low'),
    })];
    expect(rowMatchesSearch({ sev: 'crit' }, [], 'critical', cols)).toBe(true);
  });

  it('a falsy-but-real value is searchable', () => {
    const cols = [col({ key: 'count' })];
    expect(rowMatchesSearch({ count: 0 }, [], '0', cols)).toBe(true);
    expect(rowMatchesSearch({ count: 0 }, ['count'], '0')).toBe(true);
  });

  it('an object cell never matches — "[object Object]" would hit every row', () => {
    const cols = [col({ key: 'meta' })];
    expect(rowMatchesSearch({ meta: { a: 1 } }, [], 'object', cols)).toBe(false);
    expect(rowMatchesSearch({ meta: { a: 1 } }, ['meta'], 'object')).toBe(false);
  });

  it('an array of primitives is searchable by any element', () => {
    const cols = [col({ key: 'tags' })];
    expect(rowMatchesSearch({ tags: ['hazmat', 'tanker'] }, [], 'tanker', cols)).toBe(true);
  });
});

describe('tabMatch — the scope predicate (isolation)', () => {
  const columns = [
    col({ key: 'category' }),
    col({ key: 'priority' }),
  ];
  const rows: Row[] = [
    { category: 'Camera', priority: 'Critical', name: 'Cam A' },
    { category: 'Camera', priority: 'Low', name: 'Cam B' },
    { category: 'Tablet', priority: 'Critical', name: 'Tab A' },
    { category: 'ELD', priority: 'Low', name: 'ELD A' },
  ];

  it('scopes to a single column filter — nothing else can leak in', () => {
    const view: SavedTab = { id: '1', name: 'Cameras', filters: [{ id: 'category', value: ['Camera'] }] };
    const match = tabMatch(view, columns, []);
    const scoped = rows.filter(match);
    expect(scoped.map(r => r.name)).toEqual(['Cam A', 'Cam B']);
    // the isolation guarantee: a Tablet/ELD is NOT in the scoped set at all
    expect(scoped.some(r => r.category !== 'Camera')).toBe(false);
  });

  it('ANDs multiple filters', () => {
    const view: SavedTab = {
      id: '2', name: 'Critical cameras',
      filters: [{ id: 'category', value: ['Camera'] }, { id: 'priority', value: ['Critical'] }],
    };
    const scoped = rows.filter(tabMatch(view, columns, []));
    expect(scoped.map(r => r.name)).toEqual(['Cam A']);
  });

  it('combines filters with the captured search', () => {
    const view: SavedTab = {
      id: '3', name: 'Critical A', filters: [{ id: 'priority', value: ['Critical'] }], search: 'cam',
    };
    const scoped = rows.filter(tabMatch(view, columns, ['name']));
    expect(scoped.map(r => r.name)).toEqual(['Cam A']); // Critical AND name~cam
  });

  it('ignores a filter for a column that no longer exists (stale view still scopes)', () => {
    const view: SavedTab = {
      id: '4', name: 'Old',
      filters: [{ id: 'category', value: ['Camera'] }, { id: 'gone', value: ['x'] }],
    };
    const scoped = rows.filter(tabMatch(view, columns, []));
    expect(scoped.map(r => r.name)).toEqual(['Cam A', 'Cam B']); // 'gone' filter ignored
  });
});

describe('tabIsEmpty', () => {
  it('blocks saving a view that constrains nothing', () => {
    expect(tabIsEmpty([], '')).toBe(true);
    expect(tabIsEmpty([], '   ')).toBe(true);
    expect(tabIsEmpty([{ id: 'category', value: ['Camera'] }], '')).toBe(false);
    expect(tabIsEmpty([], 'search')).toBe(false);
  });
});
