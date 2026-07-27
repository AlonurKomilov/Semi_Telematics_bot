/**
 * The Alerts board's grid state ⇄ URL state adapters.
 *
 * These two functions are the whole seam between "what the operator
 * ticked in a column menu" and "what the server is asked for".  They're
 * pure, so they're pinned here rather than through a rendered grid — and
 * they're worth pinning because both directions have a silent failure
 * mode: an empty selection that reads as a filter for nothing (an empty
 * board), or a filter that quietly drops when it round-trips.
 */
import { describe, it, expect } from 'vitest';
import { toGridFilters, fromGridFilters } from './gridFilterAdapters';

describe('toGridFilters (URL → grid)', () => {
  it('omits a dimension set to "all" — that is the ABSENCE of a filter', () => {
    // Not `[{id:'alert_type', value:['all']}]`: the grid would show an
    // "all" chip and tick a box that matches no row.
    expect(toGridFilters('all', 'all')).toEqual([]);
  });

  it('carries a single value', () => {
    expect(toGridFilters('fault', 'all')).toEqual([
      { id: 'alert_type', value: ['fault'] },
    ]);
  });

  it('splits a comma list back into the multi-select shape', () => {
    expect(toGridFilters('fault,health', 'critical,warning')).toEqual([
      { id: 'alert_type', value: ['fault', 'health'] },
      { id: 'severity', value: ['critical', 'warning'] },
    ]);
  });

  it('treats an empty string as no filter', () => {
    expect(toGridFilters('', '')).toEqual([]);
  });
});

describe('fromGridFilters (grid → URL)', () => {
  it('joins a multi-select back into the comma list the API expects', () => {
    expect(fromGridFilters([
      { id: 'alert_type', value: ['fault', 'health'] },
    ])).toEqual({ typeFilter: 'fault,health', severityFilter: 'all' });
  });

  it('an emptied selection returns to "all", never an empty filter', () => {
    // Unticking the last box must widen the board, not blank it.
    expect(fromGridFilters([
      { id: 'alert_type', value: [] },
    ])).toEqual({ typeFilter: 'all', severityFilter: 'all' });
  });

  it('a dimension the grid dropped entirely returns to "all"', () => {
    // Removing the chip removes the entry, rather than emptying it.
    expect(fromGridFilters([
      { id: 'severity', value: ['critical'] },
    ])).toEqual({ typeFilter: 'all', severityFilter: 'critical' });
  });

  it('ignores filters for columns this page does not push to the server', () => {
    expect(fromGridFilters([
      { id: 'vehicle_name', value: ['T-100'] },
    ])).toEqual({ typeFilter: 'all', severityFilter: 'all' });
  });

  it('round-trips', () => {
    const url = { typeFilter: 'fault,parking', severityFilter: 'info' };
    expect(fromGridFilters(toGridFilters(url.typeFilter, url.severityFilter)))
      .toEqual(url);
  });
});
