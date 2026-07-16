/**
 * Browser-local store for the composer's PENDING chat attachments.
 *
 * Policy (docs/architecture/ai-import-assistant.md §3, owner directive):
 * the FILE lives on the user's device — never server disk, never the DB.
 * Its text rides inline on the chat request each time a message is sent
 * while the chip is attached; the server parses it transiently and keeps
 * only what the user later APPROVES (the proposal's staged rows).
 *
 * This store exists so an attached file survives panel close / page
 * refresh while the user is still composing.  Same quota discipline as
 * thoughtStore: hard caps + oldest-first eviction, and any storage
 * failure degrades to "not attached" rather than breaking the chat.
 */

/** Mirrors the backend's per-attachment cap (MAX_CONTENT_CHARS). */
export const MAX_ATTACHMENT_CHARS = 1_400_000;
/** Mirrors the backend's per-request cap (MAX_ATTACHMENTS). */
export const MAX_ATTACHMENTS = 3;
// Total store budget — localStorage's ~5MB origin quota is shared with
// the thought store (2.5MB), so pending attachments get a slice of the
// rest.  One max-size file always fits; a second/third evicts oldest.
const MAX_TOTAL_CHARS = 2_000_000;

const STORE_KEY = '4truck:ai-attachments:v1';

export interface PendingAttachment {
  name: string;
  /** Raw file text (CSV). */
  content: string;
  /** Attached-at epoch ms — eviction order. */
  t: number;
}

function load(): PendingAttachment[] {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (a): a is PendingAttachment =>
        !!a && typeof a.name === 'string' && typeof a.content === 'string',
    );
  } catch {
    return [];
  }
}

function persist(list: PendingAttachment[]): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(list));
  } catch {
    // Quota exceeded (other origin data) — the attachment stays usable
    // in memory for THIS session; it just won't survive a refresh.
    try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
  }
}

export function loadPendingAttachments(): PendingAttachment[] {
  return load();
}

/**
 * Add a file. Returns the new list (plus the names of any files evicted
 * to fit the total budget — the composer tells the user, never a silent
 * loss), or an error key the caller translates: 'too_large' | 'limit'.
 * A single over-cap file is refused, never truncated (mirrors the
 * backend's law).
 */
export function addPendingAttachment(
  current: PendingAttachment[], name: string, content: string,
): { list: PendingAttachment[]; evicted: string[] } | { error: 'too_large' | 'limit' } {
  if (content.length > MAX_ATTACHMENT_CHARS) return { error: 'too_large' };
  // Re-attaching the same name replaces it (the natural "fixed the sheet,
  // attached again" loop).
  let list = current.filter((a) => a.name !== name);
  if (list.length >= MAX_ATTACHMENTS) return { error: 'limit' };
  list = [...list, { name: name.slice(0, 120), content, t: Date.now() }];
  const evicted: string[] = [];
  while (
    list.length > 1
    && list.reduce((n, a) => n + a.content.length, 0) > MAX_TOTAL_CHARS
  ) {
    list = [...list].sort((a, b) => a.t - b.t);
    evicted.push(list[0].name);
    list = list.slice(1);
  }
  persist(list);
  return { list, evicted };
}

export function removePendingAttachment(
  current: PendingAttachment[], name: string,
): PendingAttachment[] {
  const list = current.filter((a) => a.name !== name);
  persist(list);
  return list;
}

export function clearPendingAttachments(): void {
  try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
}
