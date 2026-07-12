import { createContext, useContext, useState, useEffect, useMemo, useCallback, type ReactNode } from 'react';
import { useAuth } from './AuthContext';
import { apiJSON, setActiveViewForApi } from '../api/client';
import type { Permissions } from '../types';

const VIEW_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatch',
  hr: 'HR',
  accounting: 'Accounting',
  recruiter: 'Recruiter',
  driver: 'Driver',
};

// NOTE: no per-persona icon map here.  The PersonaSelector deliberately
// shows NO role icon (labels only — see its header comment), and the
// dashboard's icon vocabulary is lucide-react, never emoji (design.md
// §"Icons").  Emoji role glyphs live ONLY in the backend
// capabilities/permissions/roles.py for the Telegram bot, whose
// design language is emoji-native (design.md §9).

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
  safety: '/scorecards',
  dispatcher: '/live-map',
  // HR lands on Drivers — the daily entry point for compliance work
  // (CDL expirations, document review).  Accounting lands on Cost
  // Reports — same logic, the cost-rollup view is the day's start.
  hr: '/workforce/drivers',
  accounting: '/cost-reports',
  // Recruiter (+ its manager, via the persona alias) lands on Drivers —
  // the roster + qualification files are the daily entry point for
  // onboarding work.
  recruiter: '/workforce/drivers',
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
// Managers are NOT here: "manager" is a per-user tier (is_manager) on the
// base role, not a distinct dashboard view.  A recruiter manager IS a
// recruiter for presentation; only its permission set differs.
const PREVIEWABLE_ROLES = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'hr', 'accounting', 'recruiter'];

// localStorage key — survives reloads so the dashboard reopens in the
// same persona the user last chose.  We deliberately do NOT clear it on
// logout: the next login resets activeView to the user's real role anyway
// (see the useEffect below).
const STORAGE_KEY = 'roleView.activeView';
// Preview tier for manager-capable roles (Manager vs Employee) — persisted so
// re-picking the role keeps the operator's last choice.
const TIER_STORAGE_KEY = 'roleView.previewAsManager';

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
  hr: 'hr',
  accounting: 'accounting',
  recruiter: 'recruiter',
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
  hr: `hr.${APEX_DOMAIN}`,
  accounting: `accounting.${APEX_DOMAIN}`,
  recruiter: `recruiter.${APEX_DOMAIN}`,
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

// Mirror of capabilities/permissions/roles.TIER_GRANTS — labels for the
// tier switcher UI, plus SEED grants kept only as a legacy fallback: the
// manager-tier PREVIEW reads the tier's stored row from
// /admin/permissions/roles (``{role}__manager`` key — owner-editable, the
// matrix is the source of truth); the grants here apply only if that key is
// absent (older backend).  A REAL senior's permissions come from /user/me,
// so drift here never affects a live user's access.  Keep labels in sync.
interface RoleTierMirror { senior: string; base: string; grants: string[] }
const ROLE_TIERS: Record<string, RoleTierMirror> = {
  recruiter:  { senior: 'Manager', base: 'Employee', grants: ['can_invite', 'can_manage_carrier_directory'] },
  admin:      { senior: 'Full admin', base: 'Standard admin',
                grants: ['can_manage_integrations', 'can_manage_storage', 'can_manage_permissions',
                         'can_manage_account', 'can_manage_work_hours'] },
  fleet:      { senior: 'Manager', base: 'Employee', grants: ['can_invite', 'can_manage_work_hours', 'can_risk_report_all'] },
  safety:     { senior: 'Manager', base: 'Employee', grants: ['can_manage_scorecard_rules', 'can_invite'] },
  dispatcher: { senior: 'Manager', base: 'Employee', grants: ['can_manage_work_hours', 'can_manage_poi_layers', 'can_invite'] },
  hr:         { senior: 'Manager', base: 'Employee', grants: ['can_manage_work_hours', 'can_manage_applications', 'can_convert_to_driver'] },
  accounting: { senior: 'Manager', base: 'Employee', grants: ['can_work_orders_all', 'can_maintenance_all'] },
};
const roleSupportsManager = (role: string) => role in ROLE_TIERS;

interface RoleViewContextValue {
  activeView: string;
  viewLabel: string;
  homeRoute: string;
  canSwitch: boolean;
  availableViews: { key: string; label: string; supportsManager: boolean; tier?: { senior: string; base: string } }[];
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
  /** True when the active preview role has a manager tier (e.g. Recruiter),
   * so the switcher can offer a Manager/Employee toggle. */
  activeViewSupportsManager: boolean;
  /** Senior/base tier labels for the active view (null when it has no tier)
   * — e.g. Manager/Employee, or Full admin/Standard admin for Admin. */
  activeViewTier: { senior: string; base: string } | null;
  /** While previewing a manager-capable role: view it as the MANAGER tier
   * (default true — Owners should see everything) or the plain employee. */
  previewAsManager: boolean;
  setPreviewAsManager: (v: boolean) => void;
}

const RoleViewContext = createContext<RoleViewContextValue | null>(null);

export function RoleViewProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  // Real role stays optional — ``undefined`` while fetchUser is in
  // flight.  We deliberately do NOT default to a sentinel role here,
  // because the old ``?? 'driver'`` default produced a brief render
  // frame where Owner pages painted with activeView='driver' before
  // the corrective useEffect could fire.  That frame is the "stuck
  // in Driver view on refresh" symptom: it wasn't stuck, it was a
  // one-frame paint that was just enough to capture in a screenshot
  // on slow renders.  ``activeView`` below is now DERIVED rather
  // than stored as useState-then-useEffect, so it transitions in
  // the same render cycle as ``realRole`` — no flicker possible.
  const realRole = user?.role;
  const canSwitch = realRole ? SWITCHABLE_ROLES.includes(realRole) : false;

  // Preview tier for a manager-capable role: MANAGER (default — an Owner should
  // see the full experience) or plain employee.  Persisted, so re-picking the
  // role keeps the operator's last choice.
  const [previewAsManager, setPreviewAsManagerState] = useState<boolean>(() => {
    try { const v = localStorage.getItem(TIER_STORAGE_KEY); return v === null ? true : v === '1'; }
    catch { return true; }
  });
  const setPreviewAsManager = useCallback((v: boolean) => {
    setPreviewAsManagerState(v);
    try { localStorage.setItem(TIER_STORAGE_KEY, v ? '1' : '0'); } catch { /* localStorage disabled */ }
  }, []);

  // The user's explicit "preview as X" choice — the ONLY thing that
  // needs imperative state, because it persists across renders until
  // the operator picks a different persona.  Loaded from localStorage
  // on mount; mutated by switchView (which also writes the new value
  // back to localStorage).  Anything invalid in localStorage (e.g. a
  // stale 'driver' from an older code path) is rejected at read time
  // AND removed so the dead value doesn't linger.
  const [savedChoice, setSavedChoice] = useState<string | null>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && PREVIEWABLE_ROLES.includes(saved)) return saved;
      if (saved) localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* localStorage disabled */
    }
    return null;
  });

  // activeView is fully derived — recomputed in every render from
  // (realRole, canSwitch, savedChoice, current hostname).  No
  // useState, no useEffect chase, no flicker frame.
  //
  // Resolution order (switchable users only):
  //   1. Explicit user choice (savedChoice)
  //   2. Branded subdomain hint (fleet./dispatch./safety.)
  //   3. User's real role
  // Non-switchable users always see their real role.
  //
  // While ``realRole`` is still undefined (user not loaded yet),
  // activeView resolves to 'owner' — a safe placeholder that never
  // renders because App.tsx is showing the loading spinner during
  // that window.  Using a defined value keeps every consumer's
  // ``activeView`` typed as ``string`` instead of ``string |
  // undefined``, which avoids a cascade of optional-chaining noise
  // through downstream components.
  const activeView = useMemo(() => {
    if (!realRole) return 'owner';
    if (!canSwitch) return realRole;
    if (savedChoice && PREVIEWABLE_ROLES.includes(savedChoice)) return savedChoice;
    const subRole = getSubdomainRole();
    if (subRole && subRole in VIEW_LABELS) return subRole;
    return realRole;
  }, [realRole, canSwitch, savedChoice]);

  // Mirror activeView into the api/client module so apiFetch can
  // attach the ``X-View-As`` header on every authenticated request.
  // Without this the backend sees only the JWT role and persona-aware
  // counts (Overview hero card, Alerts tab badge) ignore which
  // workspace the user is currently operating in.  The header is the
  // contract between the SPA's role-switcher and the backend's
  // active_view dep.
  useEffect(() => {
    setActiveViewForApi(activeView);
  }, [activeView]);

  const [rolePermSets, setRolePermSets] = useState<Record<string, Partial<Permissions>>>({});
  // Tracks whether the role-perms fetch has SETTLED (success or failure).
  // While pending, previews stay empty (skeleton — no full-nav flash);
  // once settled, a missing role set falls back to the previewer's own
  // permissions.  Without the fallback a standard admin (no
  // can_manage_permissions → the fetch 403s) would preview a permanently
  // blank sidebar — including the automatic subdomain preview.
  const [rolePermsSettled, setRolePermsSettled] = useState(false);

  // Fetch all role permission sets from backend (Owner/Admin only)
  const fetchRolePerms = useCallback(async () => {
    if (!canSwitch) return;
    try {
      const data = await apiJSON<{ current: Record<string, Partial<Permissions>> }>(
        '/admin/permissions/roles',
      );
      setRolePermSets(data.current);
    } catch {
      // Not authorized (stock admin) or transient failure — previews fall
      // back to the user's own permissions once `rolePermsSettled` flips.
    } finally {
      setRolePermsSettled(true);
    }
  }, [canSwitch]);

  useEffect(() => {
    fetchRolePerms();
  }, [fetchRolePerms]);

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

    setSavedChoice(role);
    try {
      localStorage.setItem(STORAGE_KEY, role);
    } catch {
      /* localStorage disabled */
    }
  }, [canSwitch]);

  // For the active view, use server-fetched permission sets if available.
  const activeViewSupportsManager = canSwitch && roleSupportsManager(activeView);
  const isSelfView = activeView === realRole;
  // Self view = the user's OWN resolved permissions (/user/me — already
  // includes their real tier).  A PREVIEW uses the fetched role sets ONLY:
  // while /admin/permissions/roles is still loading, perms stay EMPTY
  // (minimal nav skeleton) instead of falling back to the previewing
  // Owner's own full set — that fallback painted EVERY feature for a
  // flash frame on refresh before snapping to the role's real nav.
  const baseViewPerms: Partial<Permissions> = (!canSwitch || isSelfView)
    ? (user?.permissions ?? {})
    : (rolePermSets[activeView]
        ?? (rolePermsSettled ? (user?.permissions ?? {}) : {}));
  // Previewing a manager-capable role AS its manager tier → use the tier's
  // OWN STORED permission row (``{role}__manager`` from the same endpoint;
  // tiers are independently owner-editable, so the matrix — not the
  // hardcoded seed mirror — is the source of truth).  The mirror overlay
  // survives only as a fallback for an older backend that doesn't return
  // tier rows yet.  Never applied to the self view: a real user's tier is
  // already resolved into their own permissions.
  const viewPerms: Partial<Permissions> = (activeViewSupportsManager && previewAsManager && !isSelfView)
    ? (rolePermSets[`${activeView}__manager`]
        ?? (Object.keys(baseViewPerms).length
          ? {
              ...baseViewPerms,
              ...(Object.fromEntries((ROLE_TIERS[activeView]?.grants ?? []).map((f) => [f, true])) as Partial<Permissions>),
            }
          : {}))
    : baseViewPerms;

  const viewHas = (flag: string) => !!viewPerms[flag as keyof Permissions];
  const viewHasAny = (...flags: string[]) => flags.some((f) => !!viewPerms[f as keyof Permissions]);

  // Non-switchable users see a single static pill showing their own
  // role.  During the loading window where realRole is undefined we
  // return an empty list — the consumer hides the pill entirely, and
  // App.tsx is showing the spinner anyway so no shell is visible.
  const availableViews = canSwitch
    ? PREVIEWABLE_ROLES.map(key => ({
        key,
        label: VIEW_LABELS[key] ?? key,
        supportsManager: roleSupportsManager(key),
        tier: ROLE_TIERS[key] ? { senior: ROLE_TIERS[key].senior, base: ROLE_TIERS[key].base } : undefined,
      }))
    : realRole
    ? [{
        key: realRole,
        label: VIEW_LABELS[realRole] ?? realRole,
        supportsManager: roleSupportsManager(realRole),
        tier: ROLE_TIERS[realRole] ? { senior: ROLE_TIERS[realRole].senior, base: ROLE_TIERS[realRole].base } : undefined,
      }]
    : [];

  const viewLabel = VIEW_LABELS[activeView] ?? activeView;
  const homeRoute = VIEW_HOME_ROUTE[activeView] ?? '/';
  const isPreviewing = canSwitch && activeView !== realRole;

  return (
    <RoleViewContext.Provider value={{
      activeView, viewLabel, homeRoute, canSwitch, availableViews,
      switchView, viewHas, viewHasAny, isPreviewing,
      rolePermSets, refreshPermissions: fetchRolePerms,
      activeViewSupportsManager,
      activeViewTier: ROLE_TIERS[activeView]
        ? { senior: ROLE_TIERS[activeView].senior, base: ROLE_TIERS[activeView].base }
        : null,
      previewAsManager, setPreviewAsManager,
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
