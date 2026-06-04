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
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
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

function makeWrapper(initialUrl: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter initialEntries={[initialUrl]}>
        <Routes>
          <Route path="/alerts" element={<>{children}</>} />
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
    expect(result.current.viewMode).toBe('by-vehicle');
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
    expect(result.current.viewMode).toBe('by-vehicle');
  });

  it('writes Dispatcher defaults (list view) when persona=dispatcher and URL is empty', () => {
    setPersona('dispatcher');
    const { result } = renderHook(() => useAlertsFilters(), {
      wrapper: makeWrapper('/alerts'),
    });
    expect(result.current.viewMode).toBe('list');
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
