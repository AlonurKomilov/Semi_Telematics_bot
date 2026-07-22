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
