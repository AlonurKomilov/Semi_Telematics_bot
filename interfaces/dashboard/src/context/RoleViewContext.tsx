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

// Glyph shown alongside the label in the top-bar selector — borrowed
// from capabilities/iam/permissions.py ROLE_DISPLAY so bot + dashboard
// agree on the persona iconography.
const VIEW_ICONS: Record<string, string> = {
  owner: '👑',
  admin: '🔑',
  fleet: '🔧',
  safety: '🛡️',
  dispatcher: '📡',
  driver: '🚛',
};

// Default landing route per persona — when an Owner/Admin switches to
// "view as Fleet", we navigate to the Fleet persona's home page so the
// preview lands where that role would normally start their day.
// Owner/Admin keep landing on Overview (account-wide health card).
const VIEW_HOME_ROUTE: Record<string, string> = {
  owner: '/',
  admin: '/',
  fleet: '/fleet/map',
  safety: '/safety/scorecards',
  dispatcher: '/safety/alerts',
  driver: '/',
};

const SWITCHABLE_ROLES = ['owner', 'admin'];

// Roles offered in the "View dashboard as…" dropdown for Owner/Admin
// previewing.  Driver is intentionally excluded: drivers use the
// Telegram Mini App, not the desktop dashboard, so a "preview as
// Driver" view here would be misleading (it'd show a stripped-down
// dashboard the actual driver would never open).  If a Driver does
// happen to log into the dashboard directly, they'll still see their
// own role label correctly via the non-switchable static pill.
const PREVIEWABLE_ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher'];

// localStorage key — survives reloads so the dashboard reopens in the
// same persona the user last chose.  We deliberately do NOT clear it on
// logout: the next login resets activeView to the user's real role anyway
// (see the useEffect below).
const STORAGE_KEY = 'roleView.activeView';

interface RoleViewContextValue {
  activeView: string;
  viewLabel: string;
  viewIcon: string;
  homeRoute: string;
  canSwitch: boolean;
  availableViews: { key: string; label: string; icon: string }[];
  switchView: (role: string) => void;
  viewHas: (flag: string) => boolean;
  viewHasAny: (...flags: string[]) => boolean;
  /** True when the current view differs from the user's real role
   * (Owner/Admin previewing as Fleet/Safety/etc).  Used by the layout
   * to render a "Previewing as X" banner. */
  isPreviewing: boolean;
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

  // Initial activeView: localStorage preference (only if user can switch
  // AND the persisted value is a known role) → fall back to user's real
  // role.  This way an Owner who picked "Fleet view" yesterday reopens
  // the dashboard already in Fleet view.
  const [activeView, setActiveView] = useState(() => {
    if (!canSwitch) return realRole;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && saved in VIEW_LABELS) return saved;
    } catch {
      /* localStorage disabled — fall through */
    }
    return realRole;
  });
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

  // If the user's real role changes (e.g. log out + log in as a different
  // account), reset activeView so we don't carry over the previous user's
  // persona preference.  Non-switchable users always see their real role.
  useEffect(() => {
    if (!canSwitch) {
      setActiveView(realRole);
      return;
    }
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (!saved || !(saved in VIEW_LABELS)) setActiveView(realRole);
    } catch {
      setActiveView(realRole);
    }
  }, [realRole, canSwitch]);

  const switchView = useCallback((role: string) => {
    if (!canSwitch) return;
    if (!(role in VIEW_LABELS)) return;
    setActiveView(role);
    try {
      localStorage.setItem(STORAGE_KEY, role);
    } catch {
      /* localStorage disabled */
    }
  }, [canSwitch]);

  // For the active view, use server-fetched permission sets if available
  const viewPerms = canSwitch
    ? (rolePermSets[activeView] ?? user?.permissions ?? {})
    : (user?.permissions ?? {});

  const viewHas = (flag: string) => !!viewPerms[flag as keyof Permissions];
  const viewHasAny = (...flags: string[]) => flags.some((f) => !!viewPerms[f as keyof Permissions]);

  const availableViews = canSwitch
    ? PREVIEWABLE_ROLES.map(key => ({
        key,
        label: VIEW_LABELS[key] ?? key,
        icon: VIEW_ICONS[key] ?? '',
      }))
    : [{
        key: realRole,
        label: VIEW_LABELS[realRole] ?? realRole,
        icon: VIEW_ICONS[realRole] ?? '',
      }];

  const viewLabel = VIEW_LABELS[activeView] ?? activeView;
  const viewIcon = VIEW_ICONS[activeView] ?? '';
  const homeRoute = VIEW_HOME_ROUTE[activeView] ?? '/';
  const isPreviewing = canSwitch && activeView !== realRole;

  return (
    <RoleViewContext.Provider value={{
      activeView, viewLabel, viewIcon, homeRoute, canSwitch, availableViews,
      switchView, viewHas, viewHasAny, isPreviewing,
      rolePermSets, refreshPermissions: fetchRolePerms,
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
