/**
 * Sidebar generator — builds the nav for a persona from the feature
 * catalog, replacing the six hand-written `*Nav.ts` files.
 *
 * One filter decides visibility (module AND permission in the same place,
 * so there's no second, parallel module filter):
 *   • the feature's module(s) intersect the persona's visible modules
 *     (strict role binding — Fleet sees Fleet tools; Owner/Admin see the
 *     account and switch persona for operational work),
 *   • the account has at least one of those modules enabled,
 *   • the user holds one of the feature's permissions (null = open),
 *   • the feature isn't `navHidden` (route-only, e.g. Invites → a tab).
 */
import type { LucideIcon } from 'lucide-react';
import {
  FEATURE_CATALOG,
  isPathModuleEnabled,
  type NavGroupKey,
  type CatalogFeature, UNIT_VIEW_VERBS,
} from '../../config/featureCatalog';

// The shape the Sidebar renders (moved here from the old defaultNav.ts).
export interface NavItem {
  labelKey: string;
  path: string;
  icon: LucideIcon;
  permission: string | string[] | null;
  /** Indented sub-entries (Settings-style) — folded from catalog
   *  entries whose ``parentId`` points at this item's feature. */
  children?: NavItem[];
}
export interface NavGroup {
  titleKey: string | null;
  items: NavItem[];
  /** Render as ONE collapsible parent entry (the Settings feature's
   *  components) instead of a flat labelled section. */
  collapsible?: boolean;
  /** The group's own page (the Settings feature's general-config page,
   *  /admin/settings): the parent row navigates HERE — "Settings" and
   *  "Account Settings" are one thing, not a group plus a duplicate
   *  child entry.  Absent when the user lacks can_manage_account; the
   *  parent then falls back to a non-navigating expander. */
  parentItem?: NavItem;
}

// Which catalog modules each persona's sidebar surfaces.  `core` is
// universal; the rest is the persona's own department.  Owner/Admin get
// `account` (configuration) but NOT department modules — they reach
// operational tools by switching persona.
const PERSONA_MODULES: Record<string, string[]> = {
  owner:      ['core', 'account'],
  admin:      ['core', 'account'],
  fleet:      ['core', 'fleet'],
  dispatcher: ['core', 'dispatch'],
  safety:     ['core', 'safety'],
  hr:         ['core', 'hr'],
  accounting: ['core', 'accounting'],
  // Recruiter (driver acquisition / onboarding) shares the HR module so
  // the Drivers surface can appear once an owner grants the permission in
  // the matrix; core-only would hide it regardless of permission.
  recruiter:  ['core', 'hr'],
  driver:     ['core'],
};

// Render order + section headers.  `null` header = no title (pinned top /
// bottom).  Empty groups are dropped.
const NAV_GROUP_ORDER: { key: NavGroupKey; titleKey: string | null; collapsible?: boolean }[] = [
  { key: 'main',       titleKey: null },
  { key: 'operations', titleKey: 'nav.operations_group' },
  { key: 'monitoring', titleKey: 'nav.monitoring_group' },
  { key: 'reports',    titleKey: 'nav.reports_group' },
  { key: 'people',     titleKey: 'nav.people_group' },
  { key: 'costs',      titleKey: 'nav.costs_group' },
  { key: 'account',    titleKey: 'nav.account_group' },
  // Settings is ONE feature; its components nest under one collapsible
  // parent — only the components the user's flags grant are listed.
  { key: 'settings',   titleKey: 'nav.settings_group', collapsible: true },
  { key: 'governance', titleKey: 'nav.governance_group' },
  { key: 'tail',       titleKey: null },
];

const asArray = (p: string | string[] | null): string[] =>
  p == null ? [] : Array.isArray(p) ? p : [p];

// Own-scope ("baseline crumb") flags — the assigned-vehicle / self-service
// variants every low-scope role carries.  These deliberately do NOT pull a
// cross-department feature into the sidebar (a recruiter's
// assigned-width maintenance access shouldn't surface Maintenance on the recruiting
// dashboard); only an ACCOUNT-WIDE grant does.
const OWN_SCOPE_FLAG = /(_vehicle|_own)$/;

export function generateNav(
  activeView: string,
  has: (...flags: string[]) => boolean,
  enabledModules: string[] | undefined,
  /** The member's unit width from ``/me`` (Team Management's answer).
   *  ``undefined`` = not loaded: treated as wide, because the sidebar
   *  is discovery, not a boundary — every route and API gates on its
   *  own.  Narrowing the nav on a missing value would hide granted
   *  features from a wide member during the first paint. */
  vehicleScope?: 'all' | 'assigned',
): NavGroup[] {
  const visibleModules = new Set(PERSONA_MODULES[activeView] ?? ['core']);
  // Department personas surface CROSS-DEPARTMENT features they hold an
  // account-wide grant for (the Permissions matrix is the source of truth —
  // a granted feature must be reachable from the nav, not just by URL).
  // Owner/Admin are exempt: they hold everything, and their sidebar stays
  // the curated account view (they switch persona for operational work).
  const crossGrantsSurface = !visibleModules.has('account');

  const inScope = (f: CatalogFeature): boolean => {
    if (f.navHidden) return false;
    if (!isPathModuleEnabled(f.path, enabledModules)) return false;
    const flags = asArray(f.permission);
    if (f.modules.some((m) => visibleModules.has(m))) {
      return flags.length === 0 || has(...flags);
    }
    // Outside the persona's own department: surfaces ONLY via an
    // account-wide grant (never the own-vehicle/self baseline crumbs,
    // and never for permissionless features).
    if (!crossGrantsSurface) return false;
    // A unit VIEW verb surfaces cross-department only for a member
    // whose width is 'all' — Team Management's answer, not a flag; the
    // pair that used to carry width is gone from the catalog.  The
    // `_own` self-service crumbs of the person-scope family still
    // never surface cross-department.
    const unitViews = flags.filter((k) => UNIT_VIEW_VERBS.includes(k));
    if (unitViews.length > 0) {
      return vehicleScope !== 'assigned' && has(...unitViews);
    }
    const accountWide = flags.filter((k) => !OWN_SCOPE_FLAG.test(k));
    return accountWide.length > 0 && has(...accountWide);
  };

  const groups: NavGroup[] = [];
  for (const g of NAV_GROUP_ORDER) {
    const inGroup = FEATURE_CATALOG.filter((f) => f.navGroup === g.key && inScope(f));
    const byId = new Map(inGroup.map((f) => [f.id, f]));
    const itemOf = (f: (typeof inGroup)[number]): NavItem =>
      ({ labelKey: f.labelKey, path: f.path, icon: f.icon, permission: f.permission });
    // Children fold under their parent when the parent is visible for
    // this persona; an orphaned child stays a flat entry (a grant must
    // never become unreachable because its parent was filtered out).
    const items: NavItem[] = inGroup
      .filter((f) => !(f.parentId && byId.has(f.parentId)))
      .map((f) => {
        const item = itemOf(f);
        const kids = inGroup.filter((c) => c.parentId === f.id).map(itemOf);
        if (kids.length) item.children = kids;
        return item;
      });
    if (items.length) {
      let parentItem: NavItem | undefined;
      let rest = items;
      if (g.collapsible) {
        parentItem = items.find((i) => i.path === '/settings');
        if (parentItem) rest = items.filter((i) => i !== parentItem);
      }
      groups.push({ titleKey: g.titleKey, items: rest, collapsible: g.collapsible, parentItem });
    }
  }
  return groups;
}
