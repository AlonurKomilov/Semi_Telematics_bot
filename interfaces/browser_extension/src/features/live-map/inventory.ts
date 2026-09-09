/**
 * What is aboard ONE truck.
 *
 * Asked per selection, like the provider links beside it — nobody wants
 * the contents of thirty trucks they did not click, and the map refreshes
 * every thirty seconds.
 *
 * Unlike a provider link, this ANSWER GOES STALE: somebody marks a
 * dashcam missing while the panel is open.  So it is cached with a life,
 * not for the life of the panel.
 */
import { apiFetch } from '../../api/client';

export interface InventoryItem {
  id: number;
  category: string;
  label: string;
  status: string;
}

export interface Onboard {
  items: InventoryItem[];
  /** How many of them are in a status that wants somebody's attention. */
  attention: number;
}

/** Long enough that opening and closing the section is free, short
 *  enough that a truck someone is working on tells the truth. */
const TTL_MS = 60_000;

const cache = new Map<number, { at: number; data: Onboard }>();

/** A 403 means the owner did not grant this person Inventory.  That is
 *  not an error to retry — it is an answer, and it will be the same
 *  answer for every truck, so the panel stops asking. */
let denied = false;

/** The statuses that mean "somebody should look at this", server-side
 *  ATTENTION_STATUSES kept in step.  Ordered worst-first: that order is
 *  what the list sorts by, so the thing to act on is the thing on top. */
const ATTENTION_ORDER = ['missing', 'damaged', 'in_repair', 'needs_check'] as const;

export function isAttention(status: string): boolean {
  return (ATTENTION_ORDER as readonly string[]).includes(status);
}

/** What colour a status earns. Absent from the ladder = nothing wrong. */
export function statusTone(status: string): 'danger' | 'warn' | 'ok' | 'muted' {
  if (status === 'missing' || status === 'damaged') return 'danger';
  if (status === 'in_repair' || status === 'needs_check') return 'warn';
  if (status === 'installed') return 'ok';
  return 'muted';                                  // spare, and anything new
}

/** ``needs_check`` → ``Needs check``.  The vocabulary is OPEN on the
 *  category side (an account may invent "safety_equipment"), so this
 *  reads whatever arrives rather than a fixed table — with the few
 *  acronyms that would otherwise come out as "Eld". */
const ACRONYMS: Record<string, string> = { eld: 'ELD', gps: 'GPS', dvr: 'DVR' };

export function humanize(token: string): string {
  const parts = String(token || '').split('_').filter(Boolean);
  if (parts.length === 0) return '';
  return parts
    .map((w, i) => (ACRONYMS[w] ?? (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)))
    .join(' ');
}

/** Worst first, then the server's own order (category, label, id). */
export function sortForPanel(items: InventoryItem[]): InventoryItem[] {
  const rank = (s: string) => {
    const i = (ATTENTION_ORDER as readonly string[]).indexOf(s);
    return i === -1 ? ATTENTION_ORDER.length : i;
  };
  return items
    .map((it, i) => ({ it, i }))
    .sort((a, b) => rank(a.it.status) - rank(b.it.status) || a.i - b.i)
    .map((x) => x.it);
}

/** Drop what is remembered — a fresh sign-in may be a different person
 *  with different grants, and their trucks are not this one's. */
export function forgetInventory(): void {
  cache.clear();
  denied = false;
}

export async function inventoryFor(
  registryId: number | null | undefined,
  now: number = Date.now(),
): Promise<Onboard | null> {
  if (denied || registryId == null) return null;
  const hit = cache.get(registryId);
  if (hit && now - hit.at < TTL_MS) return hit.data;
  try {
    const res = await apiFetch(
      `/extension/inventory?vehicle=${encodeURIComponent(String(registryId))}`);
    if (res.status === 403) { denied = true; return null; }
    if (!res.ok) return null;                      // transient: ask again next time
    const out = (await res.json()) as { items?: InventoryItem[]; attention?: number };
    const data: Onboard = {
      items: Array.isArray(out.items) ? out.items : [],
      attention: typeof out.attention === 'number' ? out.attention : 0,
    };
    cache.set(registryId, { at: now, data });
    return data;
  } catch {
    // A slow or unreachable API costs the section, never the card.
    return null;
  }
}
