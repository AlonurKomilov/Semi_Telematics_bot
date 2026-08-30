/**
 * Reports which alerts were ACTUALLY on the screen.
 *
 * "Seen" is only honest if it means eyes could have landed on the row —
 * never "the page loaded, mark a hundred seen". So a row reports itself
 * through ONE shared IntersectionObserver: it must be ≥50% visible for
 * ≥1s before its id is queued, and ids flush in batches so a fast
 * scroll through the board costs a couple of requests, not hundreds.
 *
 * A module singleton, deliberately: the observer, the dwell timers, the
 * queue and the this-session dedupe have to survive the cells that
 * register with them — a per-cell observer would be recreated on every
 * page change and re-report the same rows.
 *
 * Failure is silence. Seen is a garnish on the board, and a delivery
 * hiccup must never surface as an error on a page that is really about
 * the alerts themselves.
 */
import { apiJSON } from '@/api/client';

const DWELL_MS = 1000;
const FLUSH_MS = 3000;
const FLUSH_AT = 25;

const reported = new Set<number>();       // this session — never re-sent
const queue = new Set<number>();
const dwell = new Map<Element, ReturnType<typeof setTimeout>>();
const idOf = new WeakMap<Element, number>();
const onReported = new Map<number, () => void>();

let flushTimer: ReturnType<typeof setTimeout> | null = null;
let observer: IntersectionObserver | null = null;

function flush() {
  flushTimer = null;
  if (queue.size === 0) return;
  const ids = [...queue];
  queue.clear();
  apiJSON('/alerts/seen', { method: 'POST', body: { ids } }).catch(() => {
    /* garnish — the next view of the same rows tries again */
    ids.forEach((i) => reported.delete(i));
  });
}

function enqueue(id: number) {
  if (reported.has(id)) return;
  reported.add(id);
  queue.add(id);
  onReported.get(id)?.();
  onReported.delete(id);
  if (queue.size >= FLUSH_AT) {
    if (flushTimer) { clearTimeout(flushTimer); }
    flush();
    return;
  }
  if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
}

function getObserver(): IntersectionObserver | null {
  if (observer) return observer;
  if (typeof IntersectionObserver === 'undefined') return null;   // tests/SSR
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const id = idOf.get(e.target);
      if (id === undefined) continue;
      if (e.isIntersecting) {
        // Visible — start the dwell clock; leaving early cancels it, so
        // a fling through the list marks nothing.
        if (!dwell.has(e.target)) {
          dwell.set(e.target, setTimeout(() => {
            dwell.delete(e.target);
            enqueue(id);
          }, DWELL_MS));
        }
      } else {
        const t = dwell.get(e.target);
        if (t) { clearTimeout(t); dwell.delete(e.target); }
      }
    }
  }, { threshold: 0.5 });
  return observer;
}

/** Watch one row element. Returns an unobserve cleanup. */
export function observeSeen(el: Element, alertId: number,
                            onSeen?: () => void): () => void {
  if (reported.has(alertId)) { onSeen?.(); return () => {}; }
  const obs = getObserver();
  if (!obs) return () => {};
  idOf.set(el, alertId);
  if (onSeen) onReported.set(alertId, onSeen);
  obs.observe(el);
  return () => {
    obs.unobserve(el);
    const t = dwell.get(el);
    if (t) { clearTimeout(t); dwell.delete(el); }
    onReported.delete(alertId);
  };
}

/** Direct report — the drawer open and the bell rows, where being on
 *  screen is already certain and a dwell clock would be theatre. */
export function reportSeen(alertId: number) {
  enqueue(alertId);
}

export function wasSeenThisSession(alertId: number): boolean {
  return reported.has(alertId);
}
