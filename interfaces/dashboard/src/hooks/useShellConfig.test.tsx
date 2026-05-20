import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

/**
 * The hook reads from two contexts.  Rather than mounting real
 * providers (which call APIs and read localStorage), we mock the two
 * source hooks directly.  Each test file pins one (realRole,
 * activeView) combination; expanding coverage means adding another
 * test file with a different mock set.  This keeps each scenario
 * obvious in the diff.
 */
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { role: 'owner' } }),
}));
vi.mock('../context/RoleViewContext', () => ({
  useRoleView: () => ({ activeView: 'safety' }),
}));

import { useShellConfig } from './useShellConfig';

describe('useShellConfig (Owner previewing as Safety)', () => {
  it('keeps real-role booleans pinned to the auth user, not the preview', () => {
    const { result } = renderHook(() => useShellConfig());
    expect(result.current.isOwner).toBe(true);
    expect(result.current.isOwnerOrAdmin).toBe(true);
    expect(result.current.isDriver).toBe(false);
    expect(result.current.isAdmin).toBe(false);
  });

  it('tracks the active view independently for persona-tuned copy', () => {
    const { result } = renderHook(() => useShellConfig());
    expect(result.current.isSafetyView).toBe(true);
    expect(result.current.isFleetView).toBe(false);
    expect(result.current.briefingLabel).toBe('Safety Briefing');
    expect(result.current.chatSubject).toBe('safety & coaching');
    expect(result.current.aiSubjectAll).toBe('trucks under watch');
  });
});
