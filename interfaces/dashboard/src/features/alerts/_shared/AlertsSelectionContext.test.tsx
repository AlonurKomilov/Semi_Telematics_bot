/**
 * Tests for AlertsSelectionContext — shared selection + bulk UI state.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  AlertsSelectionProvider,
  useAlertsSelection,
} from './AlertsSelectionContext';

afterEach(cleanup);

function withProvider({ children }: { children: ReactNode }) {
  return <AlertsSelectionProvider>{children}</AlertsSelectionProvider>;
}

describe('AlertsSelectionContext', () => {
  it('starts with empty selection + expandedVehicles + clean bulk state', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    expect(result.current.selected.size).toBe(0);
    expect(result.current.expandedVehicles.size).toBe(0);
    expect(result.current.bulkError).toBe('');
    expect(result.current.acking).toBe(false);
  });

  it('setSelected updates the selection set', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    act(() => {
      result.current.setSelected(new Set(['a', 'b', 1]));
    });
    expect(result.current.selected.size).toBe(3);
    expect(result.current.selected.has('a')).toBe(true);
    expect(result.current.selected.has(1)).toBe(true);
  });

  it('clearSelection wipes the selection set', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    act(() => result.current.setSelected(new Set(['a', 'b'])));
    expect(result.current.selected.size).toBe(2);
    act(() => result.current.clearSelection());
    expect(result.current.selected.size).toBe(0);
  });

  it('setBulkError + setAcking drive transient bulk-action state', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    act(() => {
      result.current.setAcking(true);
      result.current.setBulkError('Network failed');
    });
    expect(result.current.acking).toBe(true);
    expect(result.current.bulkError).toBe('Network failed');
  });

  it('expandedVehicles works independently of selection', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    act(() => result.current.setExpandedVehicles(new Set(['truck-1'])));
    expect(result.current.expandedVehicles.has('truck-1')).toBe(true);
    expect(result.current.selected.size).toBe(0);
  });

  it('outside any provider, returns a no-op API (does not throw)', () => {
    const { result } = renderHook(() => useAlertsSelection());
    expect(result.current.selected.size).toBe(0);
    // No-op setters don't throw
    expect(() => result.current.setSelected(new Set(['a']))).not.toThrow();
    expect(() => result.current.clearSelection()).not.toThrow();
    // Drill-in setters also no-op safely outside the provider tree —
    // important because AlertsResults' #id button always calls them
    // (even on personas whose layout doesn't include the drawer).
    expect(() => result.current.openDrillIn({ id: 1 } as never)).not.toThrow();
    expect(() => result.current.closeDrillIn()).not.toThrow();
    expect(result.current.drillInAlert).toBeNull();
  });

  it('drillInAlert starts null', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    expect(result.current.drillInAlert).toBeNull();
  });

  it('openDrillIn sets the target, closeDrillIn clears it', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    const sample = { id: 42, vehicle_name: 'Truck 4', alert_type: 'events' };
    act(() => result.current.openDrillIn(sample as any));
    expect(result.current.drillInAlert?.id).toBe(42);
    act(() => result.current.closeDrillIn());
    expect(result.current.drillInAlert).toBeNull();
  });

  it('openDrillIn called twice keeps the most recent target', () => {
    const { result } = renderHook(() => useAlertsSelection(), {
      wrapper: withProvider,
    });
    act(() => result.current.openDrillIn({ id: 1 } as any));
    act(() => result.current.openDrillIn({ id: 2 } as any));
    expect(result.current.drillInAlert?.id).toBe(2);
  });
});
