import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { useAuth } from './AuthContext';
import { apiJSON } from '../api/client';
import type { Permissions } from '../types';

const VIEW_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatch',
  driver: 'Driver',
};

const SWITCHABLE_ROLES = ['owner', 'admin'];

interface RoleViewContextValue {
  activeView: string;
  viewLabel: string;
  canSwitch: boolean;
  availableViews: { key: string; label: string }[];
  switchView: (role: string) => void;
  viewHas: (flag: string) => boolean;
  viewHasAny: (...flags: string[]) => boolean;
  /** All role permission sets from the server (for Owner/Admin). */
  rolePermSets: Record<string, Partial<Permissions>>;
  /** Reload permission sets from server (e.g. after editing). */
  refreshPermissions: () => void;
}

const RoleViewContext = createContext<RoleViewContextValue | null>(null);

export function RoleViewProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const realRole = user?.role ?? 'driver';
  const canSwitch = SWITCHABLE_ROLES.includes(realRole);

  const [activeView, setActiveView] = useState(realRole);
  const [rolePermSets, setRolePermSets] = useState<Record<string, Partial<Permissions>>>({});

  // Fetch all role permission sets from backend (Owner/Admin only)
  const fetchRolePerms = useCallback(async () => {
    if (!canSwitch) return;
    try {
      const data = await apiJSON<{ current: Record<string, Partial<Permissions>> }>(
        '/admin/permissions/roles',
      );
      setRolePermSets(data.current);
    } catch {
      // Not authorized or not available — use user's own permissions
    }
  }, [canSwitch]);

  useEffect(() => {
    fetchRolePerms();
  }, [fetchRolePerms]);

  const switchView = useCallback((role: string) => {
    if (canSwitch) setActiveView(role);
  }, [canSwitch]);

  // For the active view, use server-fetched permission sets if available
  const viewPerms = canSwitch
    ? (rolePermSets[activeView] ?? user?.permissions ?? {})
    : (user?.permissions ?? {});

  const viewHas = (flag: string) => !!viewPerms[flag as keyof Permissions];
  const viewHasAny = (...flags: string[]) => flags.some((f) => !!viewPerms[f as keyof Permissions]);

  const availableViews = canSwitch
    ? Object.entries(VIEW_LABELS).map(([key, label]) => ({ key, label }))
    : [{ key: realRole, label: VIEW_LABELS[realRole] ?? realRole }];

  const viewLabel = VIEW_LABELS[activeView] ?? activeView;

  return (
    <RoleViewContext.Provider value={{
      activeView, viewLabel, canSwitch, availableViews, switchView,
      viewHas, viewHasAny, rolePermSets, refreshPermissions: fetchRolePerms,
    }}>
      {children}
    </RoleViewContext.Provider>
  );
}

export function useRoleView(): RoleViewContextValue {
  const ctx = useContext(RoleViewContext);
  if (!ctx) throw new Error('useRoleView must be inside RoleViewProvider');
  return ctx;
}
