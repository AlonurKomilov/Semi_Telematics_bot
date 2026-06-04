/**
 * Tests for usePersonaLayout — the convenience hook that resolves
 * the active persona's section list.  Same fallback chain as
 * PageLayoutHost (persona → owner → admin → empty), but skips the
 * last-resort "every registry key" fallback because the hook has
 * no registry handle.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, cleanup } from '@testing-library/react';

afterEach(cleanup);
import { usePersonaLayout } from './usePersonaLayout';
import type { LayoutMap } from './types';

const mockUseShellConfig = vi.fn();
vi.mock('../../hooks/useShellConfig', () => ({
  useShellConfig: () => mockUseShellConfig(),
}));

function setPersona(persona: string) {
  mockUseShellConfig.mockReturnValue({ persona });
}

const LAYOUTS: LayoutMap = {
  owner: ['o1', 'o2'],
  admin: ['a1'],
  fleet: ['f1', 'f2', 'f3'],
};

describe('usePersonaLayout', () => {
  it('returns the active persona layout when present', () => {
    setPersona('fleet');
    const { result } = renderHook(() => usePersonaLayout(LAYOUTS));
    expect(result.current).toEqual(['f1', 'f2', 'f3']);
  });

  it('falls back to owner when persona has no entry', () => {
    setPersona('safety');
    const { result } = renderHook(() => usePersonaLayout(LAYOUTS));
    expect(result.current).toEqual(['o1', 'o2']);
  });

  it('falls back to admin when both persona and owner are missing', () => {
    setPersona('hr');
    const { result } = renderHook(() =>
      usePersonaLayout({ admin: ['a1'] }),
    );
    expect(result.current).toEqual(['a1']);
  });

  it('returns an empty array when no fallback matches', () => {
    setPersona('hr');
    const { result } = renderHook(() => usePersonaLayout({}));
    expect(result.current).toEqual([]);
  });
});
