import { describe, it, expect } from 'vitest';
import type { AnyColumn } from '../../types';
import { searchProvenance, rowMatchesSearch } from './search';

const col = (over: Partial<AnyColumn>): AnyColumn =>
  ({ key: 'k', label: 'K', ...over } as AnyColumn);

type Row = Record<string, unknown>;

/** The screenshot case: Carrier Directory, "60" typed, Solo Pay Rate
 *  hidden.  Three visible columns, all of which render "—" for it. */
const CARRIER = col({ key: 'name', label: 'Carrier' });
const LINK = col({ key: 'link', label: 'Fill link' });
const EXP = col({ key: 'exp', label: 'Accepted Experience Levels' });
const SOLO = col({ key: 'solo', label: 'Solo Pay Rate' });
const STRUCT = col({ key: 'struct', label: 'Pay Structure' });

const visibleOnly = (...keys: string[]) => (k: string) => keys.includes(k);

describe('searchProvenance', () => {
  it('names the hidden column behind a row nothing visible explains', () => {
    const rows: Row[] = [{ name: 'TESTS CARRIER', link: '', exp: '', solo: '60' }];
    const p = searchProvenance(
      rows, [CARRIER, LINK, EXP, SOLO], visibleOnly('name', 'link', 'exp'), '60',
    );
    expect(p.unexplained).toBe(1);
    expect(p.sources).toEqual([{ key: 'solo', label: 'Solo Pay Rate', rows: 1 }]);
    expect(p.fieldOnly).toBe(0);
  });

  // The whole point of the note is that it stays quiet when the operator
  // can already see why the row is there — otherwise it is a nag on
  // every search, and a nag gets ignored on the one search that matters.
  it('says nothing when a VISIBLE column shows the match', () => {
    const rows: Row[] = [{ name: 'CARRIER 60', link: '', exp: '', solo: '60' }];
    const p = searchProvenance(
      rows, [CARRIER, LINK, EXP, SOLO], visibleOnly('name', 'link', 'exp'), '60',
    );
    expect(p).toEqual({ unexplained: 0, sources: [], fieldOnly: 0 });
  });

  it('reports only the rows that are actually unexplained', () => {
    const rows: Row[] = [
      { name: 'CARRIER 60', solo: '' },   // visible hit — not counted
      { name: 'A', solo: '60' },          // hidden hit
      { name: 'B', solo: '160' },         // hidden hit (substring)
    ];
    const p = searchProvenance(rows, [CARRIER, SOLO], visibleOnly('name'), '60');
    expect(p.unexplained).toBe(2);
    expect(p.sources).toEqual([{ key: 'solo', label: 'Solo Pay Rate', rows: 2 }]);
  });

  it('ranks columns by how many rows each explains, column order breaking ties', () => {
    const rows: Row[] = [
      { name: 'A', solo: '', struct: '60/40' },
      { name: 'B', solo: '60', struct: '60/40' },
      { name: 'C', solo: '', struct: '60/40' },
    ];
    const p = searchProvenance(
      rows, [CARRIER, SOLO, STRUCT], visibleOnly('name'), '60',
    );
    expect(p.sources.map(s => s.label)).toEqual(['Pay Structure', 'Solo Pay Rate']);
    expect(p.sources.map(s => s.rows)).toEqual([3, 1]);
  });

  // Revealing a column cannot help a row that matched a ``searchKey``
  // field with no column, so the caller must be able to tell the two
  // apart before it offers a "Show column" button that would do nothing.
  it('separates rows that NO column explains', () => {
    const rows: Row[] = [
      { name: 'A', solo: '60' },                        // a column explains it
      { name: 'B', solo: '', summary: '60 months' },    // only a row field does
    ];
    const p = searchProvenance(rows, [CARRIER, SOLO], visibleOnly('name'), '60');
    expect(p.unexplained).toBe(2);
    expect(p.fieldOnly).toBe(1);
    expect(p.sources).toEqual([{ key: 'solo', label: 'Solo Pay Rate', rows: 1 }]);
  });

  it('stays silent when nothing is hidden — there is no column to offer', () => {
    const rows: Row[] = [{ name: 'A', summary: '60 months' }];
    const p = searchProvenance(rows, [CARRIER], () => true, '60');
    expect(p).toEqual({ unexplained: 0, sources: [], fieldOnly: 0 });
  });

  it('is silent with no search, no rows, or no columns', () => {
    const rows: Row[] = [{ name: 'A', solo: '60' }];
    expect(searchProvenance(rows, [CARRIER, SOLO], visibleOnly('name'), ''))
      .toEqual({ unexplained: 0, sources: [], fieldOnly: 0 });
    expect(searchProvenance([], [CARRIER, SOLO], visibleOnly('name'), '60').unexplained)
      .toBe(0);
    expect(searchProvenance(rows, [], visibleOnly('name'), '60').unexplained).toBe(0);
  });

  // Same accessor chain as the filter, or the note would name a column
  // that does not contain what the operator typed.
  it('reads a hidden column through its display accessor, like the search does', () => {
    const sev = col({
      key: 'sev',
      label: 'Severity',
      filterLabel: (r) => ((r as Row).sev === 'crit' ? 'Critical' : 'Low'),
    });
    const rows: Row[] = [{ name: 'A', sev: 'crit' }];
    const p = searchProvenance(rows, [CARRIER, sev], visibleOnly('name'), 'critical');
    expect(p.sources).toEqual([{ key: 'sev', label: 'Severity', rows: 1 }]);
    // …and the row really is in the result, by the OTHER function.
    expect(rowMatchesSearch(rows[0], [], 'critical', [CARRIER, sev])).toBe(true);
  });

  // The two functions must agree about every row the grid displays: a
  // row the filter kept must be either visibly explained or counted as
  // unexplained — never dropped by the analysis.
  it('accounts for every row the filter kept', () => {
    const cols = [CARRIER, SOLO, STRUCT];
    const rows: Row[] = [
      { name: 'CARRIER 60' },
      { name: 'A', solo: '60' },
      { name: 'B', struct: '60/40' },
      { name: 'C', solo: '60', struct: '60/40' },
    ];
    const kept = rows.filter(r => rowMatchesSearch(r, [], '60', cols));
    expect(kept).toHaveLength(4);
    const p = searchProvenance(kept, cols, visibleOnly('name'), '60');
    const visiblyExplained = kept.length - p.unexplained;
    expect(visiblyExplained).toBe(1);
    expect(p.unexplained).toBe(3);
    expect(p.fieldOnly).toBe(0);
  });
});
