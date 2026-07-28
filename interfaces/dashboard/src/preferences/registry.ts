/**
 * THE REGISTRY — single source of truth for every per-user preference.
 *
 * One entry per preference: its type, its default, whether it syncs, the
 * legacy ``localStorage`` key it used to live under, and how to sanitize
 * a stored value.  Call sites get full type inference from this file:
 *
 *     const { value, setValue } = usePreference('notif.position');
 *     //      ^? NotifPosition                  ^? (v: NotifPosition) => void
 *
 * ─── THE RULE: does my setting belong here? ──────────────────────────
 *
 *   * If any BACKEND path reads the value to act on it (DND gating
 *     alerts, timezone in bot messages, language in emails), or it
 *     affects anyone but the current user  ->  typed column / feature
 *     table, NOT here.
 *   * If it only changes how THIS user's screen renders  ->  here.
 *
 * That is why language / timezone / DND are NOT here: they are typed
 * profile fields behind ``PUT /user/preferences``, consumed by the bot
 * and the notification router.  Same for account-level ``work_hours`` —
 * owner config in its own feature table.  Backend counterpart and the
 * same rule: ``capabilities/preferences/CLAUDE.md``.
 *
 * ─── KEYS ARE FROZEN ────────────────────────────────────────────────
 *
 * A key is the address of real user data.  Renaming one ORPHANS it — the
 * entry simply stops resolving and the user silently loses their saved
 * state.  ``registry.test.ts`` pins every key string for that reason.
 * Adding entries is always safe.
 *
 * Every default/enum below was read off the call site it replaced — do
 * not "tidy" one without checking the surface that consumes it.
 */

/** Where a preference is allowed to live.
 *  - ``device`` — never leaves this browser (screen-shaped comfort
 *    settings, preview/debug affordances).  Phase 2 will not sync these.
 *  - ``synced`` — belongs to the PERSON, so it should follow them to
 *    another browser.  Declared now; inert until Phase 2 attaches the
 *    remote backend. */
// Type-only imports (no runtime dependency — the registry still owns the
// stored SHAPE, it just doesn't re-declare types the consumer already
// defines, which would let the two drift).
import type {
  VisibilityState, ColumnOrderState, ColumnPinningState,
} from '@tanstack/react-table';
import type { AggFn } from '../types';
import type { SavedTab } from '../components/datagrid/tabs/savedTabs';

export type PrefScope = 'device' | 'synced';

export interface PrefDef<T = unknown> {
  /** Value used when nothing is stored, when the stored value fails
   *  ``sanitize``, and after a reset. */
  default: T;
  scope: PrefScope;
  /** Pre-service ``localStorage`` keys, newest first.  On first read the
   *  adapter falls back through these and copies the value forward under
   *  the canonical key — lazy migration, no big-bang, no data loss. */
  legacyKeys?: readonly string[];
  /** Convert a legacy RAW string into the typed value.  Needed because
   *  the old call sites were inconsistent: some wrote ``JSON.stringify``,
   *  others a bare ``'1'``/``'0'``, an int, or a bare enum string.  Omit
   *  when the legacy value was JSON (the default path tries JSON first,
   *  then treats the raw string as the value). */
  fromLegacy?: (raw: string) => T;
  /** Guard a value coming from storage / another tab / the server.
   *  Return ``undefined`` to reject it and fall back to ``default``.
   *  This is where the enum whitelists and numeric clamps that each old
   *  call site hand-rolled now live, once. */
  sanitize?: (raw: unknown) => T | undefined;
  /** One line on what this controls — keeps the registry
   *  self-documenting and feeds a future "your settings" screen. */
  note?: string;
}

/** Identity helper that preserves the value type without making call
 *  sites write generics. */
const def = <T,>(d: PrefDef<T>): PrefDef<T> => d;

/** ``'1'``/``'0'`` (and ``'true'``) legacy booleans. */
const legacyBool = (raw: string): boolean => raw === '1' || raw === 'true';
const asBool = (v: unknown): boolean | undefined =>
  typeof v === 'boolean' ? v : undefined;
/** Enum guard from a whitelist. */
const oneOf = <T extends string>(allowed: readonly T[]) =>
  (v: unknown): T | undefined =>
    typeof v === 'string' && (allowed as readonly string[]).includes(v)
      ? (v as T)
      : undefined;

// ── Value types.  Defined HERE (not imported from the consuming
// components) so the registry is the SSOT for the stored SHAPE and has no
// runtime dependency on any feature. ────────────────────────────────────

/** Colour/density/radius triple written by the theme picker.  Stored as
 *  ONE object under a single key, exactly as before. */
export type ThemeColor = 'dark-blue' | 'dark-purple' | 'dark-green' | 'light';
export type ThemeDensity = 'compact' | 'default' | 'comfortable';
export type ThemeRadius = 'sharp' | 'rounded' | 'pill';
export interface ThemeSetting {
  color: ThemeColor;
  density: ThemeDensity;
  radius: ThemeRadius;
}

export type NotifPosition = 'top-right' | 'bottom-right' | 'bottom-center';
export type BannerLevel = 'all' | 'critical' | 'off';
export type MaintenanceViewMode = 'list' | 'calendar';
export type InviteChannel = 'telegram' | 'url' | 'email';
/** Row height shared by every DataGrid (mirrors DataGrid's Density). */
export type TableDensity = 'compact' | 'default' | 'roomy';

const THEME_DEFAULT: ThemeSetting = {
  color: 'dark-blue', density: 'default', radius: 'rounded',
};
const THEME_COLORS: ThemeColor[] = ['dark-blue', 'dark-purple', 'dark-green', 'light'];
const THEME_DENSITIES: ThemeDensity[] = ['compact', 'default', 'comfortable'];
const THEME_RADII: ThemeRadius[] = ['sharp', 'rounded', 'pill'];

/** Assistant panel width bounds — must match ``clampPanelW`` in
 *  features/ai/AssistantContext.tsx (that clamp handles the live drag;
 *  this one guards what comes back OUT of storage). */
export const PANEL_W_MIN = 320;
export const PANEL_W_MAX = 680;

export const DEFS = {
  // ── The master switch ─────────────────────────────────────────────
  // Whether THIS browser pushes/pulls the ``synced`` preferences to the
  // account.  Necessarily ``device`` scope: it decides whether syncing
  // happens at all, so it can't itself arrive over the sync channel
  // (that would let one machine silently switch another one off).
  'prefs.syncEnabled': def<boolean>({
    default: true,
    scope: 'device',
    sanitize: asBool,
    note: 'Keep personal preferences on the account so they follow you to another browser.',
  }),

  // ── Appearance ────────────────────────────────────────────────────
  // device: tied to THIS screen's size and lighting, not to the person.
  'theme': def<ThemeSetting>({
    default: THEME_DEFAULT,
    scope: 'device',
    legacyKeys: ['dashboard-theme'],
    // Merge over the default so a stored object written before a new
    // field existed still yields a complete theme (the old call site did
    // ``{ ...DEFAULT, ...JSON.parse(saved) }`` — same behaviour).
    sanitize: (v) => {
      if (typeof v !== 'object' || v === null) return undefined;
      const o = v as Partial<ThemeSetting>;
      return {
        color: THEME_COLORS.includes(o.color as ThemeColor) ? o.color as ThemeColor : THEME_DEFAULT.color,
        density: THEME_DENSITIES.includes(o.density as ThemeDensity) ? o.density as ThemeDensity : THEME_DEFAULT.density,
        radius: THEME_RADII.includes(o.radius as ThemeRadius) ? o.radius as ThemeRadius : THEME_DEFAULT.radius,
      };
    },
    note: 'Colour scheme, density and corner radius.',
  }),
  'sidebar.collapsed': def<boolean>({
    default: false,
    scope: 'device',
    legacyKeys: ['sidebar.collapsed'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Sidebar rail collapsed — depends on this screen width.',
  }),

  // ── Live-alert banners ────────────────────────────────────────────
  // synced: a personal choice about how you want to be interrupted.
  'notif.bannerLevel': def<BannerLevel>({
    // 'critical' — matches the pre-service default in alerts/bannerLevel.ts.
    // (At ~668 alerts/wk, 'all' is a wall of pop-ups.)
    default: 'critical',
    // device, per the original module's reasoning: a wall-mounted dispatch
    // screen and a cab tablet want different noise levels, so this must
    // NOT follow the person between machines.
    scope: 'device',
    legacyKeys: ['notif.bannerLevel'],
    fromLegacy: (raw) => raw as BannerLevel,
    sanitize: oneOf<BannerLevel>(['all', 'critical', 'off']),
    note: 'Which live alerts pop a banner.',
  }),
  'notif.position': def<NotifPosition>({
    default: 'top-right',
    // device: where the lane sits is a LAYOUT choice for this screen —
    // keeping the classification the original module documented rather
    // than silently promoting it to sync.  (bannerLevel below IS synced:
    // "which alerts may interrupt me" belongs to the person.)
    scope: 'device',
    legacyKeys: ['notif.position'],
    fromLegacy: (raw) => raw as NotifPosition,
    sanitize: oneOf<NotifPosition>(['top-right', 'bottom-right', 'bottom-center']),
    note: 'Where live-alert banners appear.',
  }),

  // ── Live map overlays ─────────────────────────────────────────────
  // Already server-synced before the service existed (they were on
  // useUserPreference), so the stored keys below are the SAME strings the
  // server rows already use — moving them here must not orphan anything.
  'livemap.overlay.utilheat': def<boolean>({
    default: true,
    scope: 'synced',
    sanitize: asBool,
    note: 'Utilisation heat overlay on the live map.',
  }),
  'livemap.overlay.companycolors': def<boolean>({
    default: true,
    scope: 'synced',
    sanitize: asBool,
    note: 'Per-company colour dots on the live map.',
  }),

  // ── Notification centre ───────────────────────────────────────────
  'notifications.center.filter': def<string>({
    default: '',
    scope: 'synced',
    sanitize: (v) => (typeof v === 'string' ? v : undefined),
    note: 'Last filter used in the notification centre.',
  }),

  // ── Feature view modes ────────────────────────────────────────────
  // synced: how this person prefers to work, on any machine.
  'maintenance.viewMode': def<MaintenanceViewMode>({
    default: 'list',
    scope: 'synced',
    legacyKeys: ['4truck.maintenance.viewMode'],
    fromLegacy: (raw) => (raw === 'calendar' ? 'calendar' : 'list'),
    sanitize: oneOf<MaintenanceViewMode>(['list', 'calendar']),
    note: 'Maintenance tasks as a list or a calendar.',
  }),

  // ── AI assistant panel ────────────────────────────────────────────
  // device: panel geometry is a property of the window, not the person.
  'assistant.panelWidth': def<number>({
    default: 420,
    scope: 'device',
    legacyKeys: ['assistant.panelWidth'],
    fromLegacy: (raw) => Number.parseInt(raw, 10),
    sanitize: (v) => {
      const n = typeof v === 'number' ? v : Number(v);
      if (!Number.isFinite(n)) return undefined;
      return Math.min(PANEL_W_MAX, Math.max(PANEL_W_MIN, Math.round(n)));
    },
    note: 'Assistant panel width in px.',
  }),
  // How the pivot panel and the report behind it split the width.
  // device, matching assistant.panelWidth above: this is a judgement
  // about THIS screen's real estate, not about the person — a laptop and
  // a wall display want different splits.
  'pivot.panelWidth': def<number>({
    default: 320,          // w-80, the width it shipped at
    scope: 'device',
    sanitize: (v) => {
      const n = typeof v === 'number' ? v : Number(v);
      if (!Number.isFinite(n)) return undefined;
      // Same clamp the drag handle enforces — a hand-edited or
      // cross-version value can't strand the panel off-screen.
      return Math.min(640, Math.max(240, Math.round(n)));
    },
    note: 'Pivot fields panel width in px.',
  }),
  'assistant.expanded': def<boolean>({
    default: false,
    scope: 'device',
    legacyKeys: ['assistant.expanded'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Assistant panel expanded to full height.',
  }),

  // ── DataGrid (the two keys that are NOT per-table) ────────────────
  // Density is one setting for every grid — an operator who wants tight
  // rows wants them everywhere — so it is a FIXED key, not a family.
  // Single dot on purpose: `defFor` requires two for a family key, which
  // is what keeps 'table.density' from being read as table id "density".
  'table.density': def<TableDensity>({
    default: 'default',
    scope: 'synced',
    legacyKeys: ['4truck.table.density'],
    fromLegacy: (raw) => raw as TableDensity,
    sanitize: oneOf<TableDensity>(['compact', 'default', 'roomy']),
    note: 'Row height in every table.',
  }),
  'datagrid.savedTabCoachSeen': def<boolean>({
    default: false,
    scope: 'synced',
    sanitize: asBool,
    note: 'The one-time "right-click a tab" tip has been shown.',
  }),

  // ── Dismissals ────────────────────────────────────────────────────
  // "I've seen this, stop showing it."  synced: having dismissed a
  // one-time explainer is a fact about the PERSON — being re-taught the
  // same thing on a second browser is the annoyance this prevents.
  'onboarding.dismissed': def<boolean>({
    default: false,
    scope: 'synced',
    legacyKeys: ['4truck.onboarding.dismissed'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Setup banner dismissed.',
  }),
  'alerts.routingNudgeDismissed': def<boolean>({
    default: false,
    // device — the original module documents this as a PER-BROWSER
    // dismissal ("advice, not account config"); keeping its author's call.
    scope: 'device',
    legacyKeys: ['tg_routing_nudge_dismissed'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Telegram-routing nudge dismissed.',
  }),

  // ── Remembered last choices ───────────────────────────────────────
  'invites.lastChannel': def<InviteChannel>({
    // 'telegram' preserves the muscle memory of operators who predate
    // the 3-channel split (see features/settings/Invites.tsx).
    default: 'telegram',
    scope: 'synced',
    legacyKeys: ['invites.lastChannel'],
    fromLegacy: (raw) => raw as InviteChannel,
    sanitize: oneOf<InviteChannel>(['telegram', 'url', 'email']),
    note: 'Channel pre-selected when creating an invite.',
  }),

  // ── Dispatch board sound ──────────────────────────────────────────
  // device, for the same reason as bannerLevel: a wall-mounted dispatch
  // screen should chime; a laptop in a shared office should not.
  'dispatch.soundOn': def<boolean>({
    default: false,
    scope: 'device',
    legacyKeys: ['4truck_dispatch_sound_on'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Chime when a new live alert arrives.',
  }),

  // ── Role preview (Owner/Admin "view as") ──────────────────────────
  // The previewed persona.  '' = no explicit choice (fall back to the
  // subdomain hint, then the user's real role).
  //
  // device, like previewAsManager: a preview must not follow the operator
  // to a machine where they expect their OWN view.
  //
  // Structural guard only — the DOMAIN check (is this one of
  // PREVIEWABLE_ROLES?) stays in RoleViewContext where that list lives.
  // Duplicating the list here would either drift or create a
  // registry → context import cycle.
  'roleView.activeView': def<string>({
    default: '',
    scope: 'device',
    legacyKeys: ['roleView.activeView'],
    fromLegacy: (raw) => raw,
    sanitize: (v) => (typeof v === 'string' ? v : undefined),
    note: 'Persona currently being previewed.',
  }),
  // device: a preview affordance for THIS session's window, and it must
  // not follow the operator to a machine where they expect their own view.
  'roleView.previewAsManager': def<boolean>({
    // true — an Owner previewing a role should see the FULL experience
    // (matches RoleViewContext's pre-service default, where an absent
    // key meant true).
    default: true,
    scope: 'device',
    legacyKeys: ['roleView.previewAsManager'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Preview a manager-capable role with the manager tier on.',
  }),
} satisfies Record<string, PrefDef>;

/** Every valid preference key — autocompleted at call sites. */
export type PrefKey = keyof typeof DEFS;

// ── KEY FAMILIES (dynamic keys) ──────────────────────────────────────
//
// Most preferences have one fixed key.  DataGrid's don't: it stores a set
// of them PER TABLE — `table.maintenance-tasks.visibility`,
// `table.loads.views`, … — so the key isn't knowable at registry time.
//
// A family declares the def ONCE per part; the concrete key is built by
// ``tableKey(tableId, part)``.  The produced strings must stay
// byte-identical to what DataGrid already stored, because they address
// real rows (column layouts and SAVED TABS).  `tableKey` is the only
// place those strings are constructed — the frozen test pins its output.
export const TABLE_PARTS = {
  visibility:  def<VisibilityState>({ default: {}, scope: 'synced', note: 'Hidden columns.' }),
  order:       def<ColumnOrderState>({ default: [], scope: 'synced', note: 'Column order.' }),
  pinning:     def<ColumnPinningState>({ default: { left: [], right: [] }, scope: 'synced', note: 'Pinned columns.' }),
  colWidths:   def<Record<string, number>>({ default: {}, scope: 'synced', note: 'Column widths.' }),
  groups:      def<Record<string, string | null>>({ default: {}, scope: 'synced', note: 'Column bracket groups.' }),
  rowGroup:    def<string | null>({ default: null, scope: 'synced', note: 'Column rows are grouped by.' }),
  aggregation: def<Record<string, AggFn>>({ default: {}, scope: 'synced', note: 'Footer totals per column.' }),
  pageSize:    def<number>({ default: 25, scope: 'synced', note: 'Rows per page.' }),
  // The pivot model.  ``enabled`` lives INSIDE the object so toggling
  // pivot off keeps the configuration — turning it back on restores what
  // you had rather than an empty panel.  The model's arrays are what let
  // multi-level pivoting arrive later WITHOUT changing this frozen key.
  pivot:       def<{ enabled: boolean; model: { rows: string[]; columns: string[]; values: { key: string; aggFn: AggFn }[] } } | null>({
    default: null, scope: 'synced', note: 'How this table is pivoted.',
  }),
  // The saved-tab pair.  NOTE the stored suffixes are '.views' and
  // '.defaultView' — the ORIGINAL names, kept through the view→tab
  // rename.  Renaming them orphans every saved tab.
  views:       def<SavedTab[]>({ default: [], scope: 'synced', note: 'Your saved tabs.' }),
  defaultView: def<string>({ default: '', scope: 'synced', note: 'Tab that opens by default.' }),
} satisfies Record<string, PrefDef<unknown>>;

// Page-section layout, per feature page — `page.alerts.layout`, ….
// A user's personal arrangement of a Pattern-B page's sections (the
// gear on the page writes it).  ``order`` is every section id the user
// has arranged; ``hidden`` is the subset they switched off.  Two lists
// rather than one so a section SHIPPED AFTER the user customised can be
// told apart from one they hid: absent from ``order`` = new (gets
// appended), present in ``hidden`` = a choice (stays hidden).
// Null = never customised → the persona/role default applies.
export interface PageLayoutPref {
  order: string[];
  hidden: string[];
}

/** Shape guard shared by the store's sanitize and the layout resolver.
 *  The synced store is an opaque key-value bag with no server-side shape
 *  check, so ANYTHING JSON-parseable can arrive under this key — an
 *  older client, a future shape, a corrupted row.  This value is read
 *  synchronously in page render, ABOVE every section error boundary:
 *  malformed data must degrade to "not customised", never throw, or one
 *  bad row takes the whole route down. */
export function asPageLayoutPref(v: unknown): PageLayoutPref | null {
  if (typeof v !== 'object' || v === null) return null;
  const o = v as Partial<PageLayoutPref>;
  const strings = (a: unknown): a is string[] =>
    Array.isArray(a) && a.every((x) => typeof x === 'string');
  if (!strings(o.order) || !strings(o.hidden)) return null;
  return { order: o.order, hidden: o.hidden };
}

export const PAGE_PARTS = {
  layout: def<PageLayoutPref | null>({
    default: null, scope: 'synced',
    note: 'Which sections this page shows, and their order.',
    sanitize: asPageLayoutPref,
  }),
} satisfies Record<string, PrefDef<unknown>>;

export type PagePart = keyof typeof PAGE_PARTS;
export type PagePartValue<P extends PagePart> =
  (typeof PAGE_PARTS)[P] extends PrefDef<infer T> ? T : never;

/** The one place a per-page key string is built.  FROZEN once shipped —
 *  renaming orphans every stored arrangement. */
export const pageKey = <P extends PagePart>(feature: string, part: P) =>
  `page.${feature}.${part}` as `page.${string}.${P}`;

const PAGE_KEY_RE = /^page\.(.+)\.([A-Za-z]+)$/;

export type TablePart = keyof typeof TABLE_PARTS;
export type TablePartValue<P extends TablePart> =
  (typeof TABLE_PARTS)[P] extends PrefDef<infer T> ? T : never;

/** The one place a per-table key string is built. */
export const tableKey = <P extends TablePart>(tableId: string, part: P) =>
  `table.${tableId}.${part}` as `table.${string}.${P}`;

// Two dots minimum, so the fixed key `table.density` can never match.
const TABLE_KEY_RE = /^table\.(.+)\.([A-Za-z]+)$/;

/**
 * Resolve the def for ANY key — fixed or family.  Used by the store for
 * values arriving from storage / another tab / the server, where all we
 * have is the raw key string.  Returns null for keys we don't own (the
 * store then ignores them, which is how unrelated localStorage noise
 * stays harmless).
 */
export function defFor(key: string): PrefDef<unknown> | null {
  if (Object.prototype.hasOwnProperty.call(DEFS, key)) {
    return DEFS[key as PrefKey] as PrefDef<unknown>;
  }
  const m = TABLE_KEY_RE.exec(key);
  if (m && Object.prototype.hasOwnProperty.call(TABLE_PARTS, m[2])) {
    return TABLE_PARTS[m[2] as TablePart] as PrefDef<unknown>;
  }
  const pm = PAGE_KEY_RE.exec(key);
  if (pm && Object.prototype.hasOwnProperty.call(PAGE_PARTS, pm[2])) {
    return PAGE_PARTS[pm[2] as PagePart] as PrefDef<unknown>;
  }
  return null;
}
/** The value type for a given key. */
export type PrefValue<K extends PrefKey> = (typeof DEFS)[K] extends PrefDef<infer T> ? T : never;

export const isPrefKey = (k: string): k is PrefKey =>
  Object.prototype.hasOwnProperty.call(DEFS, k);
