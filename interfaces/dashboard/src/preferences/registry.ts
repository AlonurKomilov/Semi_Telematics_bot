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

import {
  THEME_PACKS, THEME_MATERIALS, THEME_MOTIONS, THEME_ICONS, THEME_MODS,
} from '../mods/catalogue';
import { isModToken, isSafeValue, MOD_TOKENS } from '../mods/inject';
import { SOUND_PACKS } from '../mods/sound/engine';

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
// The real model type, NOT a hand-copy.  The copy that used to live
// inline here silently lacked ``sort``, so the stored value could not
// physically hold one — which is why every pivot sort click vanished.
import type { PivotModel } from '../components/datagrid/pivot/pivot';

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
/** ``{ "<callout id>": epochMs }`` — rebuilt field by field so a
 *  corrupted or hand-edited value can never reach a component.  Ids are
 *  opaque strings the server minted; we validate shape, never meaning. */
const asIdMap = (v: unknown): Record<string, number> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  const src = (v as { entries?: unknown }).entries ?? v;
  if (!src || typeof src !== 'object') return {};
  const out: Record<string, number> = {};
  for (const [k, ts] of Object.entries(src as Record<string, unknown>)) {
    if (typeof k === 'string' && k && typeof ts === 'number' && ts > 0) {
      out[k] = ts;
    }
  }
  return out;
};

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

/** Colour/radius pair written by the theme picker.  Stored as ONE
 *  object under a single key.
 *
 *  The third field used to be `density` (compact/default/comfortable).
 *  It is gone: its only output was a `data-density` attribute selecting
 *  two custom properties that nothing in the app ever read, so the
 *  picker's three chips moved zero pixels. Size replaced it.  Removing a
 *  FIELD is safe where removing a KEY would not be — `theme` stays
 *  frozen, and `sanitize` below rebuilds the object field by field, so a
 *  stored `density` is simply dropped on the next read. */
/**
 * Mode and accent are two questions, and for a long time one field
 * answered both. `dark-blue | dark-purple | dark-green | light` reads as
 * a list of four themes, but three of its entries name a mode AND an
 * accent while the fourth names only a mode — which is why "light with a
 * green accent" was not merely unsupported, it was unsayable. Light was
 * never accent-less, either: its `--primary` is chromatic blue.
 */
export type ThemeMode = 'dark' | 'light';

/**
 * Derived from the pack catalogue rather than restated. This union and
 * `THEME_ACCENTS` below were two of the six places the accent's allowed
 * set was pinned; they now read from the one list in
 * `mods/catalogue.ts`, so a new pack cannot be half-added.
 */
export type ThemeAccent = (typeof THEME_PACKS)[number]['id'];

/**
 * @deprecated The pre-split spelling. Still written, and still read on
 * the way in, so that a build without the split can be rolled back to
 * without resetting anyone — the recipe CLAUDE.md prescribes for a
 * renamed wire value. Delete one release after the split ships, together
 * with the migration branch in `sanitize` below.
 *
 * It cannot express light + purple/green; those collapse to `light`,
 * which keeps the MODE and loses the accent. That is the right way to
 * lose information here.
 */
export type ThemeColor = 'dark-blue' | 'dark-purple' | 'dark-green' | 'light';
export type ThemeRadius = 'sharp' | 'rounded' | 'pill';
/** Derived from the catalogue, like ThemeAccent — see mods/catalogue.ts. */
export type ThemeMaterial = (typeof THEME_MATERIALS)[number];
export type ThemeMotion = (typeof THEME_MOTIONS)[number];
export type ThemeIcons = (typeof THEME_ICONS)[number];
export interface ThemeSetting {
  mode: ThemeMode;
  accent: ThemeAccent;
  radius: ThemeRadius;
  /** What surfaces are made of — solid, or translucent and blurred. */
  material: ThemeMaterial;
  /** How fast the app moves. */
  motion: ThemeMotion;
  /** Icon stroke weight. Persisted like any axis, but MOD-ONLY: the
   *  panel offers no control, so it only ever changes by taking a mod.
   *  A mod that is only a bundle of chips is a shortcut, not a look. */
  icons: ThemeIcons;
  /** Whether the routed page animates in. Mod-only, and off by default. */
  entrance: boolean;
  /**
   * The mod that is INSTALLED, if any.
   *
   * Identity, not a match. It used to be recomputed from the axes —
   * elegant, and correct right up until a mod carries something that is
   * not an axis. A sound pack cannot be read back off `<html>`, so a
   * model where identity is the sum of the axes would stop a mod's
   * sounds the moment somebody nudged the corners. "Installed" has to
   * survive being edited.
   *
   * Absent means no mod, which is not the same as "the axes happen to
   * match none" — a person can build Cab by hand without installing it.
   */
  mod?: string;
  /**
   * Token values this person authored, installed over the preset.
   *
   * A NAMED field, validated, rather than "keep whatever the stored
   * object had". The sanitiser rebuilds field by field on purpose — it
   * is the migration funnel for all four read paths — and preserving
   * unnamed keys to make room for a mod would turn that discipline into
   * a hole exactly where untrusted data arrives.
   */
  tokens?: Record<string, string>;
  /** @deprecated Derived from mode+accent; never read it to decide anything. */
  color: ThemeColor;
}

/**
 * The Size multipliers — how much bigger or smaller than designed.
 *
 * `global` is the one control the picker exposes; the four axes ride it
 * and can later be dragged apart without touching the engine (they are
 * already wired end to end in tailwind.config.js).  `regions` scopes a
 * multiplier to one named area of the app; a region MULTIPLIES the
 * global rather than replacing it.
 *
 * Every number is clamped on the way in — see SIZE_MIN / SIZE_MAX.  The
 * clamp lives HERE and in the pre-paint script in index.html —
 * `themeBoot.test.ts` asserts the two agree, because a boot stamp that
 * clamped differently would paint one size and then jump to another.
 */
export type SizeRegion =
  | 'text' | 'tables' | 'controls' | 'overlays' | 'navigation' | 'assistant';
export interface SizeSetting {
  global: number;
  text: number;
  control: number;
  layout: number;
  panel: number;
  regions: Partial<Record<SizeRegion, number>>;
}

/**
 * The range the Size control offers.
 *
 * 0.85 is not a round number — it is where LEGIBILITY takes over from
 * geometry as the binding constraint. Every pointer target now carries a
 * non-scaling 24 px floor (`min-h-tap` / `h-tap`, see design.md §5.1), so
 * hit areas hold all the way down; what does not hold is type. The
 * smallest step in the app is `text-2xs` at 10 px, which renders 8.5 px at
 * 0.85 — already at the edge of what is worth reading, and Geist's stem is
 * one device pixel at 11.63 px, so below that the glyphs of the dominant
 * `text-xs` stop landing on whole pixels on a DPR-1 screen.
 *
 * Going lower is therefore a TYPE decision, not an accessibility one: it
 * needs a floor on the small type steps first, not more work on targets.
 */
export const SIZE_MIN = 0.85;
export const SIZE_MAX = 1.5;

export type NotifPosition = 'top-right' | 'bottom-right' | 'bottom-center';
export type BannerLevel = 'all' | 'critical' | 'off';
export type MaintenanceViewMode = 'list' | 'calendar';
export type InviteChannel = 'telegram' | 'url' | 'email';
/** Row height shared by every DataGrid (mirrors DataGrid's Density). */
export type TableDensity = 'compact' | 'default' | 'roomy';

// Exported for ``themeBoot.test.ts``: the pre-paint script in index.html
// re-states these literals (it runs before any module loads, so it cannot
// import them), and the test asserts the two copies still agree.  Nothing
// else should read them — call sites get the guard via ``sanitize``.
export const THEME_MODES: ThemeMode[] = ['dark', 'light'];
export const THEME_ACCENTS: ThemeAccent[] = THEME_PACKS.map((p) => p.id);
export const THEME_DEFAULT: ThemeSetting = {
  mode: 'dark', accent: 'blue', radius: 'rounded', material: 'solid',
  motion: 'default', icons: 'regular', entrance: false, color: 'dark-blue',
};

/** @deprecated Only the migration and the alias use this. */
export const THEME_COLORS: ThemeColor[] = ['dark-blue', 'dark-purple', 'dark-green', 'light'];

/** The deprecated alias, derived. One writer, so it cannot drift. */
export function themeColorAlias(mode: ThemeMode, accent: ThemeAccent): ThemeColor {
  return mode === 'light' ? 'light' : (`dark-${accent}` as ThemeColor);
}

/**
 * A stored value written before the split. `light` maps to the blue
 * accent because that is what light has always painted — index.css gives
 * it `oklch(0.52 0.2 264)`, hue 264, the same blue the dark default uses.
 */
const LEGACY_COLOR: Record<ThemeColor, { mode: ThemeMode; accent: ThemeAccent }> = {
  'dark-blue':   { mode: 'dark',  accent: 'blue' },
  'dark-purple': { mode: 'dark',  accent: 'purple' },
  'dark-green':  { mode: 'dark',  accent: 'green' },
  'light':       { mode: 'light', accent: 'blue' },
};
export const THEME_RADII: ThemeRadius[] = ['sharp', 'rounded', 'pill'];
export const THEME_MATERIAL_LIST: ThemeMaterial[] = [...THEME_MATERIALS];
export const THEME_MOTION_LIST: ThemeMotion[] = [...THEME_MOTIONS];
export const THEME_ICONS_LIST: ThemeIcons[] = [...THEME_ICONS];

/**
 * Which theme axes the PRE-PAINT script can act on.
 *
 * The boot script exists to stop a flash: it stamps what CSS reads
 * before the first frame. An axis belongs here when a wrong value for
 * one frame would be VISIBLE — mode, accent, radius, material and
 * motion are all CSS.
 *
 * `icons` and `entrance` are not, and that is why they are absent rather
 * than forgotten. Both are React-level — an icon's stroke comes from a
 * context provider, an entrance from a component that does not exist
 * until React mounts — so there is no frame in which they could be
 * wrong. Stamping them pre-paint would be ceremony.
 *
 * `themeBoot.test.ts` reads this list, and also asserts that every key
 * of THEME_DEFAULT appears either here or in its own exclusion list — so
 * a new axis forces the decision instead of quietly skipping the guard.
 */
export const PREPAINT_AXES = [
  'mode', 'accent', 'radius', 'material', 'motion', 'color',
] as const;

export const SIZE_REGIONS: SizeRegion[] = [
  'text', 'tables', 'controls', 'overlays', 'navigation', 'assistant',
];
export const SIZE_DEFAULT: SizeSetting = {
  global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {},
};

/** Clamp one multiplier. Anything unparseable falls back to 1 rather
 *  than to a bound — a corrupt value should render the app as designed,
 *  not at either extreme. */
export const clampSize = (v: unknown): number => {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return 1;
  return Math.min(SIZE_MAX, Math.max(SIZE_MIN, n));
};

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

  /**
   * Which integration cards are expanded, keyed by provider id.
   *
   * ONE object rather than a key family: the value is a single boolean
   * per provider and the family machinery (`TABLE_PARTS`, a key regex)
   * buys nothing for that. It was `localStorage.setItem(
   * `integration-card-open:${provider_id}`, …)` straight from the
   * component — a per-user render preference written outside this
   * service, which is exactly what the rule at the top of this file
   * forbids.
   *
   * No `legacyKeys`: the old form was one key PER PROVIDER, which a
   * fixed list cannot express, and what is lost is which disclosures
   * were open. That is the mildest thing a preference can lose — the
   * bar this file sets is "losing it is an annoyance", and reading the
   * old keys would mean putting a raw storage call back into the
   * component to delete one.
   */
  'integrations.cardOpen': def<Record<string, boolean>>({
    default: {},
    scope: 'device',
    sanitize: (raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
      const out: Record<string, boolean> = {};
      for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        if (typeof v === 'boolean') out[k] = v;
      }
      return out;
    },
    note: 'Which integration cards are expanded on this device.',
  }),

  /**
   * Which cue set plays. A property of the PERSON — someone who prefers
   * the blip wants it on every machine — so it syncs.
   */
  'sound.pack': def<string>({
    default: 'chime',
    scope: 'synced',
    sanitize: (v) => (typeof v === 'string' && SOUND_PACKS.some((p) => p.id === v)
      ? v : undefined),
    note: 'Which set of cues the app plays.',
  }),

  /**
   * How loud, 0 to 1, where 0 is silence.
   *
   * DEVICE scope, and not for the usual reason: this is the difference
   * between a wall display in a yard office and a laptop with
   * headphones on, which is a property of the screen rather than of the
   * person. A single synced number would follow someone from one to the
   * other and be wrong in one of them.
   *
   * Default 1, NOT 0, and that distinction was a bug before it was a
   * comment. Sound is already opt-in: `dispatch.soundOn` is a
   * device-scoped boolean defaulting to false with its own toggle in
   * the live panel. Making volume default to zero as well would have
   * double-gated it — somebody turns the toggle on, hears nothing, and
   * concludes the feature is broken.
   *
   * So this is a LEVEL, not a switch. The gates are the per-feature
   * toggles; zero here is how a person silences a screen without
   * turning each of them off.
   */
  'sound.volume': def<number>({
    default: 1,
    scope: 'device',
    sanitize: (v) => {
      const n = typeof v === 'number' ? v : Number(v);
      return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : undefined;
    },
    note: 'How loud the cues are on this screen. Zero is silent.',
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
      const radius = THEME_RADII.includes(o.radius as ThemeRadius)
        ? o.radius as ThemeRadius : THEME_DEFAULT.radius;
      // A theme stored before the material axis existed has no field, and
      // falls to `solid` — which is what it was rendering anyway.
      const material = THEME_MATERIAL_LIST.includes(o.material as ThemeMaterial)
        ? o.material as ThemeMaterial : THEME_DEFAULT.material;
      const motion = THEME_MOTION_LIST.includes(o.motion as ThemeMotion)
        ? o.motion as ThemeMotion : THEME_DEFAULT.motion;
      const icons = THEME_ICONS_LIST.includes(o.icons as ThemeIcons)
        ? o.icons as ThemeIcons : THEME_DEFAULT.icons;
      const entrance = typeof o.entrance === 'boolean' ? o.entrance : THEME_DEFAULT.entrance;
      // A stored id for a mod that no longer exists is dropped rather
      // than kept: the catalogue is ours and can shrink between
      // releases, and an id nothing resolves would show an empty chip
      // and, later, ask for assets that are not there.
      const mod = THEME_MODS.some((m) => m.id === o.mod) ? o.mod as string : undefined;

      // Sanitised through the injector's OWN validators, so the rules
      // are stated once. Storage is untrusted input like any other — a
      // value can arrive from another tab, the sync channel, or a person
      // editing localStorage by hand.
      let tokens: Record<string, string> | undefined;
      if (o.tokens && typeof o.tokens === 'object' && !Array.isArray(o.tokens)) {
        const kept: Record<string, string> = {};
        for (const [k, v] of Object.entries(o.tokens as Record<string, unknown>)) {
          if (!isModToken(k) || !isSafeValue(v)) continue;
          // Capped at the number of tokens that exist, so a corrupted
          // store cannot grow the object without bound.
          if (Object.keys(kept).length >= MOD_TOKENS.length) break;
          kept[k] = String(v).trim();
        }
        if (Object.keys(kept).length) tokens = kept;
      }

      // THE MIGRATION LIVES HERE, and only here. This sanitiser rebuilds
      // the stored object field by field and drops anything it does not
      // name, so a split that forgot this branch would hand every
      // dark-purple, dark-green and light user a Dark Blue theme on their
      // next page load, silently. It is also the single funnel for all
      // four read paths — canonical key, legacy key, cross-tab adopt, and
      // the synced cross-device blob — so one branch covers them all.
      const split = THEME_MODES.includes(o.mode as ThemeMode)
        && THEME_ACCENTS.includes(o.accent as ThemeAccent);
      const { mode, accent } = split
        ? { mode: o.mode as ThemeMode, accent: o.accent as ThemeAccent }
        : LEGACY_COLOR[o.color as ThemeColor]
          ?? { mode: THEME_DEFAULT.mode, accent: THEME_DEFAULT.accent };

      return {
        mode, accent, radius, material, motion, icons, entrance,
        ...(mod ? { mod } : {}),
        // Omitted when empty rather than stored as `{}`: "no custom
        // tokens" and "an empty set of them" should not be two states.
        ...(tokens ? { tokens } : {}),
        color: themeColorAlias(mode, accent),
      };
    },
    note: 'Colour scheme and corner radius.',
  }),

  // How much bigger than designed. `device`, and necessarily so: a
  // `synced` value is adopted only after /user/me resolves AND the bulk
  // preference read returns — two round trips AFTER the first paint — so
  // a synced size would paint every page at 1 and then visibly jump.
  // `appearance.default` below is the synced half; it holds a DEFAULT for
  // a new browser, never the applied value.
  'size': def<SizeSetting>({
    default: SIZE_DEFAULT,
    scope: 'device',
    sanitize: (v) => {
      if (typeof v !== 'object' || v === null) return undefined;
      const o = v as Partial<SizeSetting>;
      const regions: Partial<Record<SizeRegion, number>> = {};
      const raw = (o.regions ?? {}) as Record<string, unknown>;
      for (const r of SIZE_REGIONS) {
        if (raw[r] !== undefined) regions[r] = clampSize(raw[r]);
      }
      return {
        global: clampSize(o.global ?? 1),
        text: clampSize(o.text ?? 1),
        control: clampSize(o.control ?? 1),
        layout: clampSize(o.layout ?? 1),
        panel: clampSize(o.panel ?? 1),
        regions,
      };
    },
    note: 'How much bigger the interface renders on this screen.',
  }),

  // Whether appearance follows the PERSON as well as living on this
  // screen. Necessarily `device` for the same reason as
  // prefs.syncEnabled: a machine must not be able to have its syncing
  // switched off through the sync channel.
  'appearance.followMe': def<boolean>({
    default: true,
    scope: 'device',
    sanitize: asBool,
    note: 'Apply your colour, corners and size on other browsers you sign in from.',
  }),

  // The synced half. Adopted ONLY by a browser that has nothing stored
  // locally, so an established screen never jumps mid-session. Reset
  // clears this too, otherwise the next new browser would resurrect a
  // setting the user just discarded.
  'appearance.default': def<{ theme?: ThemeSetting; size?: SizeSetting } | null>({
    default: null,
    scope: 'synced',
    sanitize: (v) => (v === null || (typeof v === 'object' && !Array.isArray(v))
      ? v as { theme?: ThemeSetting; size?: SizeSetting } | null
      : undefined),
    note: 'Your appearance settings, remembered for browsers you have not used yet.',
  }),
  // Dismissal of the one-release "config moved to the gear" pointer.
  // SYNCED, not device: the user learned where config went — they should
  // not be re-taught on their laptop after dismissing it on their desktop.
  // Delete this entry and its banner once the release has landed.
  // Tour tour verdicts — done / skipped are final, snoozed
  // re-offers after SNOOZE_DAYS (components/tour/types.ts).  Keyed
  // by tour key ('maintenance.bulk_add').  Synced: a person who skipped
  // a tour on the desktop has answered it; the laptop asking again
  // would teach them to stop reading the intro.  The backend never
  // reads this — eligibility is computed client-side at page open.
  'tour.state': def<Record<string, { s: 'done' | 'skipped' | 'snoozed'; t: string }> | null>({
    default: null,
    scope: 'synced',
    // Round-trips through the server and other tabs, so rebuild it
    // field by field like every Record-shaped synced entry here.  A
    // malformed row would not crash — isEligible treats garbage as
    // "never re-offer" — but silently muting a tour forever is exactly
    // the failure this file's convention exists to catch.
    sanitize: (raw: unknown) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
      const out: Record<string, { s: 'done' | 'skipped' | 'snoozed'; t: string }> = {};
      for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        const e = v as { s?: unknown; t?: unknown } | null;
        if (!k || !e || typeof e !== 'object') continue;
        if ((e.s === 'done' || e.s === 'skipped' || e.s === 'snoozed')
            && typeof e.t === 'string' && !Number.isNaN(Date.parse(e.t))) {
          out[k] = { s: e.s, t: e.t };
        }
      }
      return Object.keys(out).length ? out : null;
    },
  }),

  'config.moved_notice_dismissed': def<boolean>({
    default: false,
    scope: 'synced',
    sanitize: asBool,
    note: 'Hides the "settings moved to the gear" pointer once dismissed.',
  }),
  // The Loads list's pickup-date window, in days back from today; 0 means
  // "all time".  ``synced`` because it describes how this PERSON works —
  // a dispatcher lives in the last few days, an accountant reconciles a
  // whole month — and that habit should follow them between machines,
  // unlike the geometry preferences above.
  'loads.rangeDays': def<number>({
    default: 7,
    scope: 'synced',
    sanitize: (v) => {
      const n = typeof v === 'number' ? v : Number(v);
      if (!Number.isFinite(n) || n < 0) return undefined;
      return Math.floor(n);
    },
    note: 'Loads list date window (days back; 0 = all time).',
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
    // Only the centre's own source keys — a stale value from a renamed
    // tab must fall back to All, not query a source that no longer exists.
    sanitize: (v) => (typeof v === 'string'
      && ['', 'applications', 'team,ai', 'system'].includes(v) ? v : undefined),
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
  // Last role opened in the Permissions "One role" lens.  device, like
  // permissions.lens: it's where THIS screen left off, not a fact about
  // the person.  Validated against the live role list at read time.
  'permissions.role': def<string>({
    default: '',
    scope: 'device',
    sanitize: (v) => (typeof v === 'string' && v.length <= 32 ? v : undefined),
    note: 'Permissions page: last role opened in the One-role lens.',
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
  // The AI thought-log note. It was `localStorage.setItem(
  // '4truck:ai-thoughts-note:v1', '1')` inside Chat.tsx — a dismissal
  // written straight past this service, with the browser-storage
  // try/catch hand-rolled at both ends. device, not synced: the note
  // explains that thought logs stay on THIS browser, so having read it
  // is a fact about this browser too.
  'ai.thoughtNoteDismissed': def<boolean>({
    default: false,
    scope: 'device',
    legacyKeys: ['4truck:ai-thoughts-note:v1'],
    fromLegacy: legacyBool,
    sanitize: asBool,
    note: 'The "thought logs stay in this browser" note has been dismissed.',
  }),
  // ── Callouts ──────────────────────────────────────────────────────
  // Two keys because they are two different acts with two different
  // owners, and one writer each is what keeps them honest.
  //
  // COLLAPSED — the strip shrank to one line.  The statement is still
  // on screen, so nothing left the reader's view and there is nothing
  // to audit; the client writes this like any other display setting.
  'callout.collapsed': def<Record<string, number>>({
    default: {},
    scope: 'synced',
    sanitize: asIdMap,
    note: 'Callouts you have collapsed to a single line.',
  }),
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
  // synced: which way a manager reads a settlement is a working style,
  // not a device trait — it should follow them to the office machine.
  'kpi.incentiveRunView': def<'sheet' | 'board'>({
    // board: the stepper's "Review & adjust" happens THERE (marking
    // days, tier-gap bars); the sheet is the verification read.
    default: 'board',
    scope: 'synced',
    sanitize: oneOf(['sheet', 'board'] as const),
    note: 'Incentive run detail opens as the settlement sheet or the dispatcher board.',
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
  pivot:       def<{ enabled: boolean; model: PivotModel } | null>({
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
