/**
 * Tests for useAlertsFilters — the URL-param wrapper hook that owns
 * the Alerts page filter state.
 *
 * Verifies:
 *   1. First-land writes persona defaults to the URL when empty
 *   2. URL with existing params overrides defaults (bookmark survival)
 *   3. Changing any non-page filter auto-resets page to 1
 *   4. setPage itself does NOT reset page (no infinite recursion)
 *   5. resetToDefaults wipes the URL and reapplies persona defaults
 *   6. ackState change does NOT clear selection (caller's job)
 *   7. `narrowed` is measured against PERSONA defaults — it gates the
 *      board's "all caught up" claim, so a false negative would let the
 *      board tell a fleet every alert is acknowledged while thousands are
 *      pending.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, act, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAlertsFilters } from './useAlertsFilters';
import { resolveFilterDefaults } from '../personaConfig';
import type { Persona } from '../../_lib/types';

afterEach(cleanup);

const mockUseShellConfig = vi.fn();
vi.mock('../../../hooks/useShellConfig', () => ({
  useShellConfig: () => mockUseShellConfig(),
}));

function defaultsFor(persona: Persona) {
  return resolveFilterDefaults(persona);
}

function setPersona(persona: string) {
  mockUseShellConfig.mockReturnValue({ persona });
}

// Records the live query string so tests can assert on params the hook
// writes (or must leave alone), not just on the values it returns.
let lastSearch = '';
function SearchRecorder() {
  lastSearch = useLocation().search;
  return null;
}

function makeWrapper(initialUrl: string) {
  lastSearch = '';
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter initialEntries={[initialUrl]}>
        <Routes>
          <Route path="/alerts" element={<><SearchRecorder />{children}</>} />
        </Routes>
      </MemoryRouter>
    );
  };
}

describe('useAlertsFilters — first-land persona defaults', () => {
  it('writes Safety defaults to URL when persona=safety and URL is empty', () => {
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    // First-load effect runs after mount; values reflect Safety defaults.
    expect(result.current.typeFilter).toBe('events');
    expect(result.current.days).toBe(7);
    expect(result.current.ackState).toBe('new');
  });

  it('writes Fleet defaults (30 days) when persona=fleet and URL is empty', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.days).toBe(30);
  });

  it('writes Dispatcher defaults (7 days, all types) when URL is empty', () => {
    setPersona('dispatcher');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.days).toBe(7);
    expect(result.current.typeFilter).toBe('all');
    expect(result.current.ackState).toBe('new');
  });

  it('does NOT overwrite URL when URL already has filter params (bookmark deep link)', () => {
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?ackState=acknowledged&days=14'),
    });
    // URL wins — safety would default to active/7d but URL pinned different.
    expect(result.current.ackState).toBe('all');
    expect(result.current.days).toBe(14);
  });
});

describe('useAlertsFilters — compound setter behavior', () => {
  it('setTypeFilter resets page to 1 automatically', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=5&typeFilter=fault'),
    });
    expect(result.current.page).toBe(5);
    act(() => result.current.setTypeFilter('health'));
    expect(result.current.typeFilter).toBe('health');
    expect(result.current.page).toBe(1);
  });

  it('setDays resets page to 1 automatically', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=3&days=7'),
    });
    act(() => result.current.setDays(30));
    expect(result.current.days).toBe(30);
    expect(result.current.page).toBe(1);
  });

  it('setPage itself does NOT trigger page reset (no recursion)', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=2'),
    });
    act(() => result.current.setPage(5));
    expect(result.current.page).toBe(5);
  });

  it('setAckState does NOT auto-clear selection (caller responsibility)', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    // setAckState's only effect is the URL ackState param + page reset.
    // Selection clear is handled by useAlertsSelection().clearSelection()
    // at the call site — verified by the absence of a clearSelection
    // arg on this setter.
    act(() => result.current.setAckState('all'));
    expect(result.current.ackState).toBe('all');
  });
});

describe('useAlertsFilters — resetToDefaults', () => {
  it('resetToDefaults restores persona defaults even after URL changes', () => {
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=fault&days=90&page=10'),
    });
    // Initial URL wins (bookmark)
    expect(result.current.typeFilter).toBe('fault');
    act(() => result.current.resetToDefaults());
    // Safety defaults are now in the URL
    expect(result.current.typeFilter).toBe('events');
    expect(result.current.days).toBe(7);
    expect(result.current.page).toBe(1);
  });
});


describe('useAlertsFilters — params this hook does not own', () => {
  // The notification bell links to a bare /alerts?alertId=N.  Landing
  // there has no filter params, so the first-load effect writes persona
  // defaults — and it used to build a fresh query string, silently
  // deleting alertId.  The deep link died a tick after arriving and the
  // alert never opened.
  it('preserves ?alertId when writing first-load defaults', async () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?alertId=11101'),
    });
    await waitFor(() => expect(result.current.days).toBe(30));   // fleet default
    expect(lastSearch).toContain('alertId=11101');
  });

  it('preserves ?alertId through resetToDefaults', async () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?alertId=11101&typeFilter=fuel'),
    });
    act(() => result.current.resetToDefaults());
    // "Clear all filters" clears FILTERS — it doesn't close the record
    // the operator is reading.
    expect(lastSearch).toContain('alertId=11101');
    expect(result.current.typeFilter).not.toBe('fuel');
  });
});


describe('useAlertsFilters — server paging state', () => {
  it('defaults to a 25-row page and triage order', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.pageSize).toBe(25);
    expect(result.current.sort).toBe('');      // '' = severity, then recency
    expect(result.current.dir).toBe('desc');
  });

  it('reads sort and page size from the URL, so a link restores the view', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?sort=vehicle_name&dir=asc&pageSize=100&page=3'),
    });
    expect(result.current.sort).toBe('vehicle_name');
    expect(result.current.dir).toBe('asc');
    expect(result.current.pageSize).toBe(100);
    expect(result.current.page).toBe(3);
  });

  it('caps page size at the server ceiling', () => {
    // The API rejects page_size > 2000; asking for more would 422 the
    // board rather than showing more rows.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?pageSize=99999'),
    });
    expect(result.current.pageSize).toBe(2000);
  });

  it('changing the SORT returns to page 1', () => {
    // Page 7 of the old order is a different set of rows in the new one.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=7'),
    });
    act(() => result.current.setSort('vehicle_name', 'asc'));
    expect(result.current.page).toBe(1);
    expect(result.current.sort).toBe('vehicle_name');
  });

  it('changing the PAGE SIZE returns to page 1', () => {
    // Page 7 of 25-row pages is past the end at 100 per page.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=7'),
    });
    act(() => result.current.setPageSize(100));
    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(100);
  });

  it('clearing the sort returns to triage order rather than an empty order', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?sort=vehicle_name&dir=asc'),
    });
    act(() => result.current.setSort('', 'desc'));
    expect(result.current.sort).toBe('');
    expect(lastSearch).not.toContain('sort=');
  });
});


describe('useAlertsFilters — saved tabs', () => {
  it('applies a tab as ONE history entry, not a write per key', async () => {
    // Written together because it's one act; separately it would fire a
    // server query per key and leave half-applied scopes in the URL.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?page=5'),
    });
    act(() => result.current.applyTab('t1', {
      typeFilter: 'fault,health',
      severityFilter: 'critical',
      vehicleSearch: 'Battle',
      ackState: 'all',
    }));
    expect(result.current.tab).toBe('t1');
    expect(result.current.typeFilter).toBe('fault,health');
    expect(result.current.severityFilter).toBe('critical');
    expect(result.current.vehicleSearch).toBe('Battle');
    expect(result.current.ackState).toBe('all');
    expect(result.current.page).toBe(1);      // a new scope starts at page 1
  });

  it('a tab survives in the URL, so it can be refreshed and shared', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t9&typeFilter=fuel'),
    });
    expect(result.current.tab).toBe('t9');
    expect(result.current.typeFilter).toBe('fuel');
  });

  it('editing a filter LEAVES the tab', () => {
    // A tab means "this exact set".  Staying highlighted while showing
    // something else would misreport what you're looking at.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault'),
    });
    act(() => result.current.setSeverityFilter('critical'));
    expect(result.current.tab).toBe('');
    expect(result.current.severityFilter).toBe('critical');   // the edit stands
  });

  it('paging and re-sorting stay INSIDE the tab', () => {
    // Navigation within a scope, not a change of scope.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault'),
    });
    act(() => result.current.setPage(3));
    expect(result.current.tab).toBe('t1');
    act(() => result.current.setPageSize(100));
    expect(result.current.tab).toBe('t1');
  });

  it('re-SORTING stays inside the tab', () => {
    // setSort writes sort/dir through its own atomic setParams; the
    // in-scope-navigation rule is what keeps the tab.  Untested before,
    // and a plausible "cleanup" refactor would have broken it silently.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault'),
    });
    act(() => result.current.setSort('vehicle_name', 'asc'));
    expect(result.current.tab).toBe('t1');
    expect(result.current.sort).toBe('vehicle_name');
  });

  it('applies a tab\'s captured ORDER in the same single write', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    act(() => result.current.applyTab('t1', {
      typeFilter: 'fault', severityFilter: 'all', vehicleSearch: '',
      sort: 'vehicle_name', dir: 'asc',
    }));
    expect(result.current.sort).toBe('vehicle_name');
    expect(result.current.dir).toBe('asc');
    expect(result.current.tab).toBe('t1');
  });

  it('a tab with NO captured order reverts to triage order', () => {
    // Otherwise it silently inherits whatever you were browsing under,
    // which isn't the view that was saved.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?sort=vehicle_name&dir=asc'),
    });
    act(() => result.current.applyTab('t2', {
      typeFilter: 'fuel', severityFilter: 'all', vehicleSearch: '',
    }));
    expect(result.current.sort).toBe('');
    expect(result.current.tab).toBe('t2');
  });

  it('resetToDefaults drops the tab, not just its filters', () => {
    // "Clear all filters" used to leave the tab lit over a board showing
    // the persona defaults — the strip naming a scope that wasn't applied.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault&sort=vehicle_name'),
    });
    act(() => result.current.resetToDefaults());
    expect(result.current.tab).toBe('');
    expect(result.current.sort).toBe('');
    expect(result.current.typeFilter).toBe(defaultsFor('fleet' as Persona).typeFilter);
  });

  it('clearTab leaves the filters the tab put there', () => {
    // Switching to a Status tab shouldn't silently widen the board — the
    // operator chose those filters by choosing the tab.
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault&severityFilter=critical'),
    });
    act(() => result.current.clearTab());
    expect(result.current.tab).toBe('');
    expect(result.current.typeFilter).toBe('fault');
    expect(result.current.severityFilter).toBe('critical');
  });
});


describe('useAlertsFilters — narrowed (gates the "all caught up" claim)', () => {
  it('is FALSE on a persona default view, even when the default is not "all"', () => {
    // Safety lands on typeFilter='events' by default — that is NOT
    // the user narrowing anything, so the genuine all-clear must stay
    // reachable for them.
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.typeFilter).toBe('events');
    expect(result.current.narrowed).toBe(false);
  });

  it('is TRUE once the user picks a type away from their default', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=fuel'),
    });
    expect(result.current.narrowed).toBe(true);
  });

  it('is TRUE for a severity filter and for a vehicle search', () => {
    setPersona('fleet');
    const sev = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?severityFilter=critical'),
    });
    expect(sev.result.current.narrowed).toBe(true);
    cleanup();
    setPersona('fleet');
    const veh = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?vehicleSearch=ZZZQQQ999'),
    });
    expect(veh.result.current.narrowed).toBe(true);
  });

  it('is FALSE for a date window alone — a window is always set', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?days=7'),
    });
    expect(result.current.narrowed).toBe(false);
  });

  it('goes back to FALSE after resetToDefaults', () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=fuel&vehicleSearch=abc'),
    });
    expect(result.current.narrowed).toBe(true);
    act(() => { result.current.resetToDefaults(); });
    expect(result.current.narrowed).toBe(false);
  });
});

describe('setGridFilters — two filters, ONE navigation', () => {
  /**
   * The bug this pins, reported from the live board: clicking a bar in
   * "Alert volume" filtered the grid to that type, and removing the
   * resulting chip did nothing — the board stayed stuck on that type.
   *
   * ``setSearchParams`` is not React state.  Its updater receives the
   * params of the render it was called from, so setTypeFilter(...) then
   * setSeverityFilter(...) in one tick both start from the SAME url and
   * the second navigation wins, silently reverting the first.
   */
  it('clearing the type filter actually clears it', async () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=fault&severityFilter=all&days=7'),
    });
    expect(result.current.typeFilter).toBe('fault');

    // Exactly what the grid reports when the Type chip's X is pressed:
    // type cleared, severity unchanged.
    act(() => result.current.setGridFilters('all', 'all'));

    await waitFor(() => expect(result.current.typeFilter).toBe('all'));
    expect(lastSearch).toContain('typeFilter=all');
  });

  it('writes both dimensions together, neither reverting the other', async () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=all&severityFilter=all&days=7'),
    });
    act(() => result.current.setGridFilters('fuel', 'critical'));
    await waitFor(() => expect(result.current.typeFilter).toBe('fuel'));
    expect(result.current.severityFilter).toBe('critical');
  });

  it('two separate setters in one tick still lose one — why the pair exists', async () => {
    /** Documents the hazard rather than the fix: if this ever starts
     *  passing, react-router changed and the atomic setter could be
     *  simplified.  Until then, any NEW pair of filter setters called
     *  together needs the same treatment. */
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?typeFilter=fault&severityFilter=all&days=7'),
    });
    act(() => {
      result.current.setTypeFilter('all');
      result.current.setSeverityFilter('all');
    });
    await waitFor(() => expect(lastSearch).toContain('severityFilter=all'));
    expect(result.current.typeFilter).toBe('fault');   // the lost write
  });

  it('leaves a saved tab, like every other hand-edited filter', async () => {
    setPersona('fleet');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?tab=t1&typeFilter=fault&severityFilter=all&days=7'),
    });
    act(() => result.current.setGridFilters('all', 'all'));
    await waitFor(() => expect(result.current.tab).toBe(''));
  });
});
