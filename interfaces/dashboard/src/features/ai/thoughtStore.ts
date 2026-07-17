/**
 * Browser-local store for AI answer thought logs (chain-of-thought +
 * process timeline).
 *
 * Policy: these are display-only artifacts for the owning user — the
 * server persists only what it functionally needs (message text for the
 * model's follow-up context, tier label for attribution).  The thought
 * log NEVER touches the DB; it arrives on the live `done` event, gets
 * stored here, and is re-attached to messages loaded from the server by
 * a content-derived key.
 *
 * Keying: `c<conversationId>:<hash(answer text prefix)>` — content-based
 * so it survives the server's row-cap pruning (which would shift any
 * index-based key) and needs no server-issued message id.
 *
 * Quota: entries are LRU-pruned by stored-at time to a max count and a
 * max serialized size, so the origin's localStorage never fills up.
 */

import type { AIProcessStep } from './types';
import type { Artifact } from './artifacts/types';

const STORE_KEY = '4truck:ai-thoughts:v1';
const MAX_ENTRIES = 200;
const MAX_BYTES = 2_500_000; // ~2.5MB of the ~5MB origin quota

interface ThoughtEntry {
  /** Flat chain-of-thought (fallback display). */
  r?: string;
  /** Ordered process timeline. */
  p?: AIProcessStep[];
  /** Follow-up suggestion chips shown under the answer. */
  s?: string[];
  /** Structured artifacts (tables/charts) rendered under the answer. */
  a?: Artifact[];
  /** Files that rode WITH a user message (name + kind only — content
   *  stays in attachmentStore) — restores the message's chips. */
  f?: { n: string; k: string }[];
  /** Stored-at epoch ms — LRU eviction order. */
  t: number;
}

type ThoughtMap = Record<string, ThoughtEntry>;

/** djb2 — tiny, stable, good enough to disambiguate answers. */
function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(36);
}

export function thoughtKey(conversationId: number | null | undefined, answerText: string): string {
  return `c${conversationId ?? 0}:${hash(answerText.slice(0, 300))}`;
}

function load(): ThoughtMap {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed as ThoughtMap : {};
  } catch {
    return {};
  }
}

function persist(map: ThoughtMap): void {
  try {
    let entries = Object.entries(map);
    // Count cap first, then size cap — both evict oldest-stored first.
    entries.sort((a, b) => b[1].t - a[1].t);
    if (entries.length > MAX_ENTRIES) entries = entries.slice(0, MAX_ENTRIES);
    let out = Object.fromEntries(entries);
    let raw = JSON.stringify(out);
    while (raw.length > MAX_BYTES && entries.length > 1) {
      entries = entries.slice(0, Math.floor(entries.length / 2));
      out = Object.fromEntries(entries);
      raw = JSON.stringify(out);
    }
    localStorage.setItem(STORE_KEY, raw);
  } catch {
    // Quota exceeded despite caps (other origin data) — drop everything
    // rather than break the chat; thought logs are a nicety.
    try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
  }
}

// Monotonic stored-at stamp — rapid saves within one millisecond would
// otherwise tie and make LRU eviction order arbitrary.
let _lastT = 0;
function _nextT(): number {
  _lastT = Math.max(Date.now(), _lastT + 1);
  return _lastT;
}

export function saveThought(
  key: string,
  data: { reasoning?: string; process?: AIProcessStep[]; suggestions?: string[]; artifacts?: Artifact[]; files?: { name: string; kind: string }[] },
): void {
  if (!data.reasoning
    && (!data.process || data.process.length === 0)
    && (!data.suggestions || data.suggestions.length === 0)
    && (!data.artifacts || data.artifacts.length === 0)
    && (!data.files || data.files.length === 0)) return;
  const map = load();
  map[key] = {
    r: data.reasoning || undefined,
    p: data.process && data.process.length > 0 ? data.process : undefined,
    s: data.suggestions && data.suggestions.length > 0 ? data.suggestions : undefined,
    a: data.artifacts && data.artifacts.length > 0 ? data.artifacts : undefined,
    f: data.files && data.files.length > 0
      ? data.files.map((x) => ({ n: x.name, k: x.kind }))
      : undefined,
    t: _nextT(),
  };
  persist(map);
}

export function getThought(
  key: string,
): { reasoning?: string; process?: AIProcessStep[]; suggestions?: string[]; artifacts?: Artifact[]; files?: { name: string; kind: string }[] } | null {
  const e = load()[key];
  if (!e) return null;
  return {
    reasoning: e.r, process: e.p, suggestions: e.s, artifacts: e.a,
    files: e.f?.map((x) => ({ name: x.n, kind: x.k })),
  };
}

/** Drop all thought logs for one conversation (per-chat delete). */
export function deleteThoughtsForConversation(conversationId: number): void {
  const map = load();
  const prefix = `c${conversationId}:`;
  let changed = false;
  for (const k of Object.keys(map)) {
    if (k.startsWith(prefix)) {
      delete map[k];
      changed = true;
    }
  }
  if (changed) persist(map);
}

/** Wipe the whole store (used by tests; available for a future "clear all"). */
export function clearThoughtStore(): void {
  try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
}
