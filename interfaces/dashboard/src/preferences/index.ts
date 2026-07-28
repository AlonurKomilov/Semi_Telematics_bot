/**
 * Preferences — the single source of truth for per-user UI state.
 *
 *   React:     const { value, setValue } = usePreference('notif.position');
 *   Non-React: preferences.get('theme') / preferences.set('theme', 'dark')
 *
 * Add a preference by adding ONE entry to ``registry.ts`` (type, default,
 * scope, legacy key).  The rule for what belongs here — and what belongs
 * in a feature table instead — is in that file's header and in
 * ``capabilities/preferences/CLAUDE.md``.  Full guide: ./CLAUDE.md
 */

export { usePreference, useTablePreference, usePagePreference, useSyncLoaded } from './usePreference';
export { pageKey, type PageLayoutPref } from './registry';
export {
  DEFS,
  isPrefKey,
  type PrefKey,
  type PrefValue,
  type PrefScope,
  type PrefDef,
  type ThemeSetting,
  type ThemeColor,
  type ThemeDensity,
  type ThemeRadius,
  type NotifPosition,
  type BannerLevel,
  type MaintenanceViewMode,
  PANEL_W_MIN,
  PANEL_W_MAX,
} from './registry';
export { LS_PREFIX } from './local';
export { measureUsage, formatBytes, type PrefUsage } from './usage';
export { createRemoteBackend } from './remote';
export {
  get,
  set,
  reset,
  resetAll,
  subscribe,
  attachBackend,
  detachBackend,
  type SyncBackend,
} from './store';

// Namespaced alias for non-React readers, so a module-level call reads
// unambiguously (``preferences.get('theme')`` rather than a bare ``get``).
import * as store from './store';
export const preferences = store;
