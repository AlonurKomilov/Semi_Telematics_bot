/**
 * Browser-local store for chat attachments — PENDING (composing) and
 * CONVERSATION-BOUND (already sent).
 *
 * Policy (capabilities/ai/docs/ai-import-assistant.md §3, owner directive):
 * the FILE lives on the user's device — never server disk, never the DB.
 * Its derived text rides inline on each chat request; the server parses
 * it transiently and keeps only what the user later APPROVES (the
 * proposal's staged rows).
 *
 * Lifecycle (mirrors the standard chat pattern the user knows):
 *   pending  — attached in the composer, not sent yet; survives panel
 *              close / refresh mid-compose; removable via ✕.
 *   convo    — sent with a message: the chip moves onto the message,
 *              the composer clears, and the file binds to that
 *              CONVERSATION so follow-up turns keep riding it (the
 *              server holds nothing between turns).  Removable from
 *              the "+" menu's "In this chat" list; dies with the
 *              thread.
 *
 * Same quota discipline as thoughtStore: one shared budget over both
 * sections, hard caps, oldest-first eviction (convo entries first —
 * they're re-derivable by re-attaching), and any storage failure
 * degrades to "not attached" rather than breaking the chat.
 */

/** Per-attachment cap in UTF-8 BYTES — bytes, not chars, because the
 *  real outer boundary (the API's 2MB body middleware) counts bytes,
 *  and non-Latin text is 2+ bytes/char.  Mirrors the backend's cap. */
export const MAX_ATTACHMENT_BYTES = 1_400_000;
/** Mirrors the backend's per-request cap (MAX_ATTACHMENTS). */
export const MAX_ATTACHMENTS = 3;
// Total budget in UTF-8 bytes across pending + all conversations —
// sized so a request's attachment set plus JSON overhead always clears
// the 2MB body ceiling, and localStorage stays healthy (the ~5MB origin
// quota is shared with the thought store's 2.5MB).
const MAX_TOTAL_BYTES = 1_800_000;

const STORE_KEY = '4truck:ai-attachments:v1';

/** UTF-8 byte length of a JS string. */
function byteLen(s: string): number {
  return new TextEncoder().encode(s).length;
}

export interface PendingAttachment {
  name: string;
  /** Derived text: CSV for sheets, extracted text for documents. */
  content: string;
  /** 'sheet' = importable spreadsheet text; 'text' = read-only document;
   *  'image' = compressed data-URL shown to vision-capable models. */
  kind: 'sheet' | 'text' | 'image';
  /** UTF-8 byte size, measured at attach time (chip label + budgets). */
  size: number;
  /** Attached-at epoch ms — eviction order. */
  t: number;
}

interface StoreShape {
  pending: PendingAttachment[];
  /** conversation id (as string key) → files bound to that thread. */
  convo: Record<string, PendingAttachment[]>;
}

function normalize(a: PendingAttachment): PendingAttachment {
  return {
    ...a,
    size: typeof a.size === 'number' ? a.size : byteLen(a.content),
    kind: a.kind === 'text' || a.kind === 'image' ? a.kind : 'sheet',
  };
}

function isAttachment(a: unknown): a is PendingAttachment {
  return !!a && typeof (a as PendingAttachment).name === 'string'
    && typeof (a as PendingAttachment).content === 'string';
}

function load(): StoreShape {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return { pending: [], convo: {} };
    const parsed = JSON.parse(raw);
    // v1 stored a bare pending array — migrate in place on first read.
    if (Array.isArray(parsed)) {
      return { pending: parsed.filter(isAttachment).map(normalize), convo: {} };
    }
    if (!parsed || typeof parsed !== 'object') return { pending: [], convo: {} };
    const shape = parsed as StoreShape;
    const convo: Record<string, PendingAttachment[]> = {};
    for (const [k, v] of Object.entries(shape.convo ?? {})) {
      if (Array.isArray(v)) convo[k] = v.filter(isAttachment).map(normalize);
    }
    return {
      pending: (Array.isArray(shape.pending) ? shape.pending : [])
        .filter(isAttachment).map(normalize),
      convo,
    };
  } catch {
    return { pending: [], convo: {} };
  }
}

function totalBytes(s: StoreShape): number {
  let n = s.pending.reduce((acc, a) => acc + a.size, 0);
  for (const list of Object.values(s.convo)) {
    n += list.reduce((acc, a) => acc + a.size, 0);
  }
  return n;
}

/** Evict oldest-first until the shared budget fits — conversation-bound
 *  entries go first (they're restorable by re-attaching the file);
 *  pending (still composing) survive longest. Returns evicted names. */
function enforceBudget(s: StoreShape): string[] {
  const evicted: string[] = [];
  while (totalBytes(s) > MAX_TOTAL_BYTES) {
    let oldestKey: string | null = null;
    let oldestIdx = -1;
    let oldestT = Infinity;
    for (const [k, list] of Object.entries(s.convo)) {
      list.forEach((a, i) => {
        if (a.t < oldestT) { oldestT = a.t; oldestKey = k; oldestIdx = i; }
      });
    }
    if (oldestKey !== null) {
      evicted.push(s.convo[oldestKey][oldestIdx].name);
      s.convo[oldestKey].splice(oldestIdx, 1);
      if (s.convo[oldestKey].length === 0) delete s.convo[oldestKey];
      continue;
    }
    if (s.pending.length > 1) {
      s.pending.sort((a, b) => a.t - b.t);
      evicted.push(s.pending[0].name);
      s.pending = s.pending.slice(1);
      continue;
    }
    break;   // a single pending file never self-evicts (per-file cap holds it sane)
  }
  return evicted;
}

function persist(s: StoreShape): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(s));
  } catch {
    // Quota exceeded (other origin data) — attachments stay usable in
    // memory for THIS session; they just won't survive a refresh.
    try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
  }
}

// ── Pending (composer) ───────────────────────────────────────────────

export function loadPendingAttachments(): PendingAttachment[] {
  return load().pending;
}

/**
 * Add a file to the composer. Returns the new pending list (plus the
 * names of any files evicted to fit the shared budget — the composer
 * tells the user, never a silent loss), or an error key the caller
 * translates: 'too_large' | 'limit'.  A single over-cap file is
 * refused, never truncated (mirrors the backend's law).
 */
export function addPendingAttachment(
  current: PendingAttachment[], name: string, content: string,
  kind: 'sheet' | 'text' | 'image' = 'sheet',
): { list: PendingAttachment[]; evicted: string[] } | { error: 'too_large' | 'limit' } {
  const size = byteLen(content);
  if (size > MAX_ATTACHMENT_BYTES) return { error: 'too_large' };
  // Re-attaching the same name replaces it (the natural "fixed the sheet,
  // attached again" loop).
  const list = current.filter((a) => a.name !== name);
  if (list.length >= MAX_ATTACHMENTS) return { error: 'limit' };
  const s = load();
  s.pending = [...list, { name: name.slice(0, 120), content, kind, size, t: Date.now() }];
  const evicted = enforceBudget(s);
  persist(s);
  return { list: s.pending, evicted };
}

export function removePendingAttachment(
  current: PendingAttachment[], name: string,
): PendingAttachment[] {
  const s = load();
  s.pending = current.filter((a) => a.name !== name);
  persist(s);
  return s.pending;
}

export function clearPendingAttachments(): void {
  const s = load();
  s.pending = [];
  persist(s);
}

/** Put files back into the composer — used when a NEW chat's send fails
 *  before a conversation exists to bind them to (never lose the file). */
export function setPendingAttachments(files: PendingAttachment[]): PendingAttachment[] {
  const s = load();
  s.pending = files.slice(-MAX_ATTACHMENTS);
  enforceBudget(s);
  persist(s);
  return s.pending;
}

// ── Conversation-bound (sent) ────────────────────────────────────────

export function loadConversationAttachments(conversationId: number): PendingAttachment[] {
  return load().convo[String(conversationId)] ?? [];
}

/**
 * Bind files to a conversation after a successful send — they auto-ride
 * every later message in that thread (the stateless equivalent of a
 * standard chat keeping the file "in the conversation").  Merged by
 * name (newest wins), capped at MAX_ATTACHMENTS newest per thread.
 */
export function bindConversationAttachments(
  conversationId: number, files: PendingAttachment[],
): PendingAttachment[] {
  const s = load();
  const key = String(conversationId);
  const merged = [...(s.convo[key] ?? [])];
  for (const f of files) {
    const i = merged.findIndex((a) => a.name === f.name);
    if (i !== -1) merged.splice(i, 1);
    merged.push(f);
  }
  merged.sort((a, b) => a.t - b.t);
  s.convo[key] = merged.slice(-MAX_ATTACHMENTS);
  enforceBudget(s);
  persist(s);
  return s.convo[key] ?? [];
}

export function removeConversationAttachment(
  conversationId: number, name: string,
): PendingAttachment[] {
  const s = load();
  const key = String(conversationId);
  s.convo[key] = (s.convo[key] ?? []).filter((a) => a.name !== name);
  if (s.convo[key].length === 0) delete s.convo[key];
  persist(s);
  return s.convo[key] ?? [];
}

/** Drop a deleted thread's files (called with the per-chat delete). */
export function clearConversationAttachments(conversationId: number): void {
  const s = load();
  delete s.convo[String(conversationId)];
  persist(s);
}

/**
 * Look up held files by exact name across pending + every conversation
 * (newest wins on duplicates).  Used post-approve to archive a write
 * action's source files onto the record it created — names are pure
 * lookup KEYS into this device-local store, never paths or URLs.
 */
export function findHeldAttachments(names: string[]): PendingAttachment[] {
  const want = new Set(names);
  const byName = new Map<string, PendingAttachment>();
  const s = load();
  const consider = (a: PendingAttachment) => {
    if (!want.has(a.name)) return;
    const prev = byName.get(a.name);
    if (!prev || a.t > prev.t) byName.set(a.name, a);
  };
  s.pending.forEach(consider);
  for (const list of Object.values(s.convo)) list.forEach(consider);
  return [...byName.values()];
}

/**
 * Files attached in OTHER conversations, still held device-locally —
 * offered as "Recent files" so a user can re-attach a sheet to a new
 * chat without hunting for it on disk again.  Deduped by name (newest
 * wins), newest first, excluding names already in the given list
 * (current chat's bound files + pending chips).
 */
export function listRecentAttachments(
  excludeConversationId: number | null,
  excludeNames: string[] = [],
  limit = 5,
): PendingAttachment[] {
  const s = load();
  const skip = new Set(excludeNames);
  const byName = new Map<string, PendingAttachment>();
  for (const [key, list] of Object.entries(s.convo)) {
    if (excludeConversationId != null && key === String(excludeConversationId)) continue;
    for (const a of list) {
      if (skip.has(a.name)) continue;
      const prev = byName.get(a.name);
      if (!prev || a.t > prev.t) byName.set(a.name, a);
    }
  }
  return [...byName.values()].sort((a, b) => b.t - a.t).slice(0, limit);
}
