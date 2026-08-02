import { describe, it, expect } from 'vitest';
import { resolveScopeBase, scopeMatch, type TabScope } from './scope';
import type { DataGridSegment } from '../../components/datagrid';
import type { AnyColumn } from '../../types';

const SEGMENTS: DataGridSegment[] = [
  { key: 'active', label: 'Active', match: (r) => r.status === 'submitted' },
  { key: 'hired', label: 'Hired', match: (r) => r.status === 'hired' },
  { key: 'closed', label: 'Closed', match: (r) => r.status === 'rejected' },
];

const COLUMNS: AnyColumn[] = [
  { key: 'status', label: 'Status', filterValue: (r) => String(r.status ?? '') } as AnyColumn,
  { key: 'city', label: 'City' } as AnyColumn,
];

const KEYS = ['first_name'];

const tab = (over: Partial<TabScope> = {}): TabScope =>
  ({ id: 't1', filters: [], search: '', ...over });

const ROWS = [
  { status: 'submitted', city: 'Chicago', first_name: 'Ann' },
  { status: 'submitted', city: 'Denver', first_name: 'Bob' },
  { status: 'hired', city: 'Chicago', first_name: 'Cal' },
  { status: 'rejected', city: 'Chicago', first_name: 'Dee' },
];
const apply = (m: ReturnType<typeof scopeMatch>) => (m ? ROWS.filter(m) : ROWS);

describe('resolveScopeBase', () => {
  it('with no tab, the base IS the lifecycle segment', () => {
    expect(resolveScopeBase('hired', null, SEGMENTS)).toBe('hired');
  });

  it('a tab is bounded by the slice it was saved under', () => {
    expect(resolveScopeBase('active', tab({ baseSegment: 'closed' }), SEGMENTS)).toBe('closed');
  });

  // Both of these mean "this tab can hold active, hired AND closed rows",
  // which is what stops the bulk bar offering a verb that only some of
  // them can take.
  it('a tab saved while on another tab is UNBOUNDED', () => {
    expect(resolveScopeBase('active', tab(), SEGMENTS)).toBeNull();
  });

  it('a STALE base is unbounded too — DataGrid drops it the same way', () => {
    expect(resolveScopeBase('active', tab({ baseSegment: 'gone' }), SEGMENTS)).toBeNull();
  });
});

describe('scopeMatch — the table and the Board must agree', () => {
  it('no tab: just the lifecycle slice', () => {
    expect(apply(scopeMatch('active', null, SEGMENTS, COLUMNS, KEYS)).map(r => r.first_name))
      .toEqual(['Ann', 'Bob']);
  });

  it('undefined (not a pass-all fn) when nothing narrows', () => {
    expect(scopeMatch(null, null, SEGMENTS, COLUMNS, KEYS)).toBeUndefined();
  });

  it('a tab COMPOSES with its base — never widens past it', () => {
    const t = tab({ baseSegment: 'active', search: 'chicago' });
    const got = apply(scopeMatch('active', t, SEGMENTS, COLUMNS, KEYS));
    // Chicago has an active, a hired and a rejected row; only the active
    // one survives, because the tab is a scope WITHIN Active.
    expect(got.map(r => r.first_name)).toEqual(['Ann']);
  });

  it('an unbounded tab spans every lifecycle slice', () => {
    const t = tab({ search: 'chicago' });
    expect(apply(scopeMatch(null, t, SEGMENTS, COLUMNS, KEYS)).map(r => r.first_name))
      .toEqual(['Ann', 'Cal', 'Dee']);
  });

  it('replays a captured COLUMN FILTER, not just the search', () => {
    const t = tab({ baseSegment: 'active', filters: [{ id: 'status', value: ['submitted'] }] });
    expect(apply(scopeMatch('active', t, SEGMENTS, COLUMNS, KEYS))).toHaveLength(2);
    const none = tab({ baseSegment: 'active', filters: [{ id: 'status', value: ['hired'] }] });
    // Composition means base AND tab: no row is both Active and hired.
    expect(apply(scopeMatch('active', none, SEGMENTS, COLUMNS, KEYS))).toHaveLength(0);
  });
});
