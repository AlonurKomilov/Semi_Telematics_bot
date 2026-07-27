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
  'assistant.expanded': def<boolean>({
    default: false,
    scope: 'device',
    legacyKeys: ['assistant.expanded'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'Assistant panel expanded to full height.',
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
/** The value type for a given key. */
export type PrefValue<K extends PrefKey> = (typeof DEFS)[K] extends PrefDef<infer T> ? T : never;

export const isPrefKey = (k: string): k is PrefKey =>
  Object.prototype.hasOwnProperty.call(DEFS, k);
