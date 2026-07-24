/**
 * Live-alert diff — the pure core of the on-screen banner watcher.
 *
 * The rules that keep it from flooding, isolated so they're testable
 * without React or a timer:
 *   • FIRST load establishes a BASELINE — every current alert is marked
 *     seen and NOTHING banners (opening the dashboard with 40 pending
 *     alerts must not fire 40 pop-ups; the bell badge carries those).
 *   • Only ids not seen before banner, and every id is marked seen the
 *     moment it's considered — so nothing re-banners on the next poll.
 *   • The level filter gates DISPLAY only, never `seen`: switching to
 *     "all" later must not replay past alerts.
 */
export interface AlertLike {
  id: string | number;
  severity?: 'critical' | 'warning' | 'info';
}

export interface DiffResult<T extends AlertLike> {
  /** New alerts that pass the level filter (caller caps how many render). */
  toShow: T[];
  /** Updated seen set — assign back to the caller's ref. */
  seen: Set<string>;
}

export function diffNewAlerts<T extends AlertLike>(
  current: readonly T[],
  seen: Set<string>,
  level: 'all' | 'critical',
  isFirstLoad: boolean,
): DiffResult<T> {
  const nextSeen = new Set(seen);
  const toShow: T[] = [];
  for (const a of current) {
    const id = String(a.id);
    if (nextSeen.has(id)) continue;
    nextSeen.add(id);                     // seen regardless of level/baseline
    if (isFirstLoad) continue;            // baseline: mark seen, show nothing
    if (level === 'critical' && a.severity !== 'critical') continue;
    toShow.push(a);
  }
  return { toShow, seen: nextSeen };
}

/**
 * Banners whose alert is no longer active — return their entries so the
 * caller can dismiss them.  A sticky critical banner never auto-closes, so
 * once its alert is acknowledged/resolved anywhere its banner must retire
 * too, rather than linger for days looking current.
 *
 * ``activeIds`` MUST be the AUTHORITATIVE active set for exactly the shown
 * ids — i.e. the response of ``activeAmong(shown.keys())`` (/alerts/
 * active-among), NOT the capped/windowed recent feed.  Feeding a truncated
 * feed here would false-dismiss a still-open critical that simply fell off
 * the page — the exact bug this signal exists to avoid.
 */
export function resolvedBanners(
  shown: ReadonlyMap<string, string | number>,
  activeIds: ReadonlySet<string>,
): Array<[string, string | number]> {
  const gone: Array<[string, string | number]> = [];
  for (const [alertId, bannerId] of shown) {
    if (!activeIds.has(alertId)) gone.push([alertId, bannerId]);
  }
  return gone;
}
