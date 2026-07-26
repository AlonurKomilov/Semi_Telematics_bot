/**
 * Live alert-banner LEVEL preference — how much pops up on screen while
 * you're on the dashboard.
 *
 *   'all'      — every new alert banners
 *   'critical' — only critical ones banner (DEFAULT) — at ~668 alerts/wk,
 *                "all" would be a wall of pop-ups; the bell badge + board
 *                already carry the rest
 *   'off'      — nothing pops up (bell + board still show everything)
 *
 * Per-DEVICE by design: a wall-mounted dispatch screen and a cab tablet
 * want different noise levels, so this is registered ``device`` scope and
 * never follows the operator between machines.
 *
 * Storage, the default, the valid-value guard, cross-tab sync and the
 * session fallback for storage-restricted contexts all live in the
 * preferences service now (``src/preferences``) — this module is a thin
 * typed facade so the existing consumers keep their imports.
 */
import { usePreference, preferences } from '../../preferences';
import type { BannerLevel } from '../../preferences';

export type { BannerLevel };

export function getBannerLevel(): BannerLevel {
  return preferences.get('notif.bannerLevel');
}

export function setBannerLevel(level: BannerLevel): void {
  // The store holds the value in memory even when localStorage throws
  // (strict private browsing) — what the old ``_memory`` fallback did.
  preferences.set('notif.bannerLevel', level);
}

export function useBannerLevel(): BannerLevel {
  return usePreference('notif.bannerLevel').value;
}

export function resetBannerLevelForTests(): void {
  preferences.reset('notif.bannerLevel');
}
