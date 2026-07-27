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

afterEach(cleanup);

const mockUseShellConfig = vi.fn();
vi.mock('../../../hooks/useShellConfig', () => ({
  useShellConfig: () => mockUseShellConfig(),
}));

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
    expect(result.current.typeFilter).toBe('safety_events');
    expect(result.current.days).toBe(7);
    expect(result.current.ackState).toBe('active');
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
    expect(result.current.ackState).toBe('active');
  });

  it('does NOT overwrite URL when URL already has filter params (bookmark deep link)', () => {
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts?ackState=acknowledged&days=14'),
    });
    // URL wins — safety would default to active/7d but URL pinned different.
    expect(result.current.ackState).toBe('acknowledged');
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
    act(() => result.current.setAckState('acknowledged'));
    expect(result.current.ackState).toBe('acknowledged');
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
    expect(result.current.typeFilter).toBe('safety_events');
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


describe('useAlertsFilters — narrowed (gates the "all caught up" claim)', () => {
  it('is FALSE on a persona default view, even when the default is not "all"', () => {
    // Safety lands on typeFilter='safety_events' by default — that is NOT
    // the user narrowing anything, so the genuine all-clear must stay
    // reachable for them.
    setPersona('safety');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.typeFilter).toBe('safety_events');
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
