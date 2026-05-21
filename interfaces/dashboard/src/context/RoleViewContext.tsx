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
//
// Dispatch lands on the Live Map because dispatchers operate
// visually from the map — they triage from the spatial view of
// what's where rather than from a list of alerts.  (Their hero
// strip surfaces pending alerts so they're not lost.)
const VIEW_HOME_ROUTE: Record<string, string> = {
  owner: '/',
  admin: '/',
  fleet: '/live-map',
  safety: '/driver-scorecards',
  dispatcher: '/live-map',
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

// Branded subdomain → persona mapping.  When an Owner/Admin opens
// fleet.4truck.us / dispatch.4truck.us / safety.4truck.us the dashboard
// loads pre-switched into that persona's view (matching nav + hero),
// even on a fresh login.  An explicit localStorage preference still wins
// — the operator can override the subdomain default and the choice
// persists.  Non-switchable users (e.g. a real Fleet user landing on
// dispatch.) keep their actual role; the subdomain is a hint, not a
// permission.
const SUBDOMAIN_TO_ROLE: Record<string, string> = {
  fleet: 'fleet',
  dispatch: 'dispatcher',
  safety: 'safety',
};

// Persona → host mapping (inverse of SUBDOMAIN_TO_ROLE plus the
// owner/admin/driver cases that share dash.).  Used by ``switchView``
// when an Owner/Admin picks a persona from the selector — the switch
// becomes a navigation to the matching subdomain so the URL bar
// reflects the active persona.  Mirrors ROLE_TO_HOST in AuthContext;
// duplicated here intentionally because adding a new persona requires
// updating BOTH the auth-redirect and the persona-selector flows.
const APEX_DOMAIN = (import.meta.env.VITE_APEX_DOMAIN as string | undefined) ?? '4truck.us';
const ROLE_HOST: Record<string, string> = {
  owner: `dash.${APEX_DOMAIN}`,
  admin: `dash.${APEX_DOMAIN}`,
  fleet: `fleet.${APEX_DOMAIN}`,
  dispatcher: `dispatch.${APEX_DOMAIN}`,
  safety: `safety.${APEX_DOMAIN}`,
  driver: `dash.${APEX_DOMAIN}`,
};

function getSubdomainRole(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const host = window.location.hostname.toLowerCase();
    const label = host.split('.')[0];
    return SUBDOMAIN_TO_ROLE[label] ?? null;
  } catch {
    return null;
  }
}

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

  // Initial activeView resolution order (switchable users only):
  //   1. Explicit localStorage preference — user already chose a view
  //   2. Branded subdomain hint — fleet./dispatch./safety. landing page
  //   3. User's real role
  // Non-switchable users always see their real role.  The subdomain
  // check sits between persistence and default so the URL acts as a
  // first-time hint without overriding a deliberate user choice.
  const [activeView, setActiveView] = useState(() => {
    if (!canSwitch) return realRole;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      // Validate against PREVIEWABLE_ROLES, not the wider VIEW_LABELS
      // (which includes ``driver``).  Driver is intentionally excluded
      // from the persona switcher — drivers use the Telegram Mini App,
      // not the desktop dashboard — so a stale ``driver`` value in
      // localStorage (from an older code path, a manual DevTools edit,
      // or pre-fix data) must NOT auto-select Driver view for an
      // Owner/Admin, which would render a stripped-down view they
      // can't navigate out of without clearing storage.
      if (saved && PREVIEWABLE_ROLES.includes(saved)) return saved;
    } catch {
      /* localStorage disabled — fall through */
    }
    const subRole = getSubdomainRole();
    if (subRole && subRole in VIEW_LABELS) return subRole;
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
  // Switchable users follow the same resolution order as the initial
  // mount: saved preference → subdomain hint → real role.
  useEffect(() => {
    if (!canSwitch) {
      setActiveView(realRole);
      return;
    }
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      // Same tightening as the useState initializer — only honor a
      // persisted preview choice if it's one we actually offer in the
      // switcher.  ``driver`` slipped through here before and stuck
      // Owner/Admin in Driver view across reloads.
      if (saved && PREVIEWABLE_ROLES.includes(saved)) return;
    } catch {
      /* localStorage disabled — fall through */
    }
    const subRole = getSubdomainRole();
    if (subRole && subRole in VIEW_LABELS) {
      setActiveView(subRole);
      return;
    }
    setActiveView(realRole);
  }, [realRole, canSwitch]);

  const switchView = useCallback((role: string) => {
    if (!canSwitch) return;
    // Defensive: only allow PREVIEWABLE roles, even though the
    // PersonaSelector UI already filters to those.  Without this,
    // any code path that ever calls ``switchView('driver')`` (test
    // code, console exploration, future bug) would save 'driver' to
    // localStorage and re-introduce the stuck-in-Driver-view bug.
    if (!PREVIEWABLE_ROLES.includes(role)) return;

    // Persona switching IS navigation.  When the target role lives on
    // a different host (Owner on dash. picking Fleet → fleet.4truck.us),
    // we navigate the browser so the URL bar reflects the active
    // persona — bookmarkable, shareable, and consistent with the
    // subdomain-auto-detect logic at mount time.  Same-host switches
    // (Owner ↔ Admin both live on dash.) stay as a local state update
    // so React Query caches and in-flight data don't get torn down for
    // a cosmetic shell-swap.
    //
    // The current pathname + query is preserved so a user on
    // dash.4truck.us/vehicles who picks "Fleet" lands on
    // fleet.4truck.us/vehicles, not Fleet's default home.  If the
    // page isn't accessible under the new persona's permissions the
    // standard permission-denied UI handles that.
    const targetHost = ROLE_HOST[role];
    if (typeof window !== 'undefined' && targetHost) {
      const currentHost = window.location.hostname.toLowerCase();
      if (currentHost !== targetHost && currentHost.endsWith(APEX_DOMAIN)) {
        const pathAndQuery = window.location.pathname + window.location.search;
        window.location.href = `https://${targetHost}${pathAndQuery}`;
        return;
      }
    }

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
