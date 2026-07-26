/**
 * How much space this user's preferences actually occupy.
 *
 * Shown on the profile so "personal preferences" isn't an invisible
 * black box — an operator can see it's a few KB of their own settings,
 * not something to worry about, and confirm what's included.
 *
 * Counts the CANONICAL entries only (``4truck.pref.*``).  Legacy
 * un-prefixed keys are deliberately excluded: they're migration
 * leftovers, not live state, and counting them would double-report.
 */

import { LS_PREFIX } from './local';

export interface PrefUsage {
  /** Bytes across all stored preference entries (key + value). */
  bytes: number;
  /** How many preference entries are actually stored (an untouched
   *  preference occupies nothing — it resolves from its default). */
  entries: number;
  /** Bytes attributable to DataGrid state (column layouts, saved tabs)
   *  — always the bulk of it, so it's worth naming separately. */
  gridBytes: number;
}

const sizeOf = (s: string): number => {
  try { return new TextEncoder().encode(s).length; } catch { return s.length; }
};

/** Measure what's in local storage right now. */
export function measureUsage(): PrefUsage {
  let bytes = 0;
  let entries = 0;
  let gridBytes = 0;
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const raw = localStorage.key(i);
      if (!raw || !raw.startsWith(LS_PREFIX)) continue;
      const value = localStorage.getItem(raw) ?? '';
      const size = sizeOf(raw) + sizeOf(value);
      bytes += size;
      entries += 1;
      if (raw.startsWith(`${LS_PREFIX}table.`)) gridBytes += size;
    }
  } catch {
    /* storage unavailable — report zero rather than throwing */
  }
  return { bytes, entries, gridBytes };
}

/** Human size: "412 B", "12.4 KB", "1.2 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}
