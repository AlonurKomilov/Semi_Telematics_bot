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
 * Per-device (localStorage + session fallback) — same store shape as the
 * banner-position preference; a wall-mounted dispatch screen and a cab
 * tablet want different noise levels.
 */
import { useSyncExternalStore } from 'react';

export type BannerLevel = 'all' | 'critical' | 'off';

const KEY = 'notif.bannerLevel';
const DEFAULT_LEVEL: BannerLevel = 'critical';
const VALID: BannerLevel[] = ['all', 'critical', 'off'];
const EVENT = 'notif-banner-level-changed';

let _memory: BannerLevel | null = null;

export function getBannerLevel(): BannerLevel {
  try {
    const v = localStorage.getItem(KEY) as BannerLevel | null;
    if (v && VALID.includes(v)) return v;
  } catch {
    /* storage unavailable — fall through to session value */
  }
  return _memory ?? DEFAULT_LEVEL;
}

export function setBannerLevel(level: BannerLevel): void {
  _memory = level;
  try {
    localStorage.setItem(KEY, level);
  } catch {
    /* storage unavailable — _memory carries this session */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(EVENT, cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener('storage', cb);
  };
}

export function useBannerLevel(): BannerLevel {
  return useSyncExternalStore(subscribe, getBannerLevel, () => DEFAULT_LEVEL);
}

export function resetBannerLevelForTests(): void {
  _memory = null;
}
