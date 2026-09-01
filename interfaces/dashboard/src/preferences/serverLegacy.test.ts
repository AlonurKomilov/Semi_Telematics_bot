/**
 * `legacyKeys` has to reach the SERVER, not just this browser.
 *
 * It used to be a localStorage-only mechanism: `readPref` falls back
 * through the chain in local.ts, but `remote.ts` PUTs the registry key
 * verbatim as the row key and adoption looked rows up by that same
 * string with no alias table. So renaming a `synced` key orphaned its
 * server row — the browser fallback rescued the one machine that still
 * held the old localStorage entry, and every other sign-in silently got
 * the default. `sound.pack` -> `mods.sound.pack` did exactly that, and
 * nothing failed.
 *
 * Nothing in the registry announced the difference, either: a `synced`
 * def with `legacyKeys` LOOKED migrated. The structural test below is
 * what makes the promise true for the next rename as well as this one.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { DEFS } from './registry';
import { LS_PREFIX } from './local';
import {
  attachBackend, detachBackend, canonicalKeyForRow, pickLegacyRows, get,
  type LegacyRow,
} from './store';

/** Every def that claims a former REGISTRY key (prefix-form legacy). */
const RENAMED = Object.entries(DEFS).flatMap(([canonical, def]) =>
  ((def as { legacyKeys?: string[] }).legacyKeys ?? [])
    .filter((l) => l.startsWith(LS_PREFIX))
    .map((l) => l.slice(LS_PREFIX.length))
    .filter((row) => row !== canonical)
    .map((row) => ({ canonical, row, scope: (def as { scope?: string }).scope })),
);

const backendOf = (rows: Record<string, unknown>) => ({
  loadAll: async () =>
    Object.entries(rows).map(([key, value]) => ({ key, value: JSON.stringify(value) })),
  put: vi.fn(),
  del: vi.fn(),
});

beforeEach(() => {
  detachBackend();
  localStorage.clear();
});

describe('a renamed key still finds its server row', () => {
  it('has something to protect — the rename really happened', () => {
    // If this ever reaches zero the suite below is vacuous, and a
    // vacuous guard is the failure mode this whole file exists to stop.
    expect(RENAMED.length).toBeGreaterThan(0);
  });

  it('maps every former registry key back to its canonical one', () => {
    for (const { canonical, row } of RENAMED) {
      expect(
        canonicalKeyForRow(row),
        `a server row keyed "${row}" is orphaned — ${canonical} claims it in legacyKeys`,
      ).toBe(canonical);
    }
  });

  it('leaves pre-service keys alone — they never had a server row', () => {
    // 'dashboard-theme' predates the preference service, so no row was
    // ever written under it. Claiming it here would invent a mapping.
    expect(canonicalKeyForRow('dashboard-theme')).toBeNull();
    expect(canonicalKeyForRow('nothing.at.all')).toBeNull();
  });

  /**
   * The behavioural half is deliberately CONCRETE. A generic version has
   * to invent a stored value, and every invented value is rejected by
   * the def's own sanitizer — the first draft asserted on 'chime__x' and
   * measured nothing but the default coming back. So it uses the one
   * real rename with a real alternative value, and the assertion above
   * keeps the pair honest if the registry moves underneath it.
   */
  const PAIR = { canonical: 'mods.sound.pack', row: 'sound.pack' };

  it('still describes a rename the registry actually has', () => {
    expect(RENAMED).toContainEqual(expect.objectContaining(PAIR));
  });

  it('adopts a legacy row under the canonical key', async () => {
    // 'blip' rather than the 'chime' default: adopting nothing at all
    // would leave the default in place and pass a weaker assertion.
    await attachBackend(backendOf({ [PAIR.row]: 'blip' }));
    expect(get(PAIR.canonical)).toBe('blip');
  });

  it('lets the canonical row win when the account carries both', async () => {
    // Listed legacy-first and canonical-first, because a bulk read gives
    // no order guarantee and the answer must not depend on one.
    await attachBackend(backendOf({ [PAIR.row]: 'blip', [PAIR.canonical]: 'chime' }));
    expect(get(PAIR.canonical)).toBe('chime');
    detachBackend();
    await attachBackend(backendOf({ [PAIR.canonical]: 'chime', [PAIR.row]: 'blip' }));
    expect(get(PAIR.canonical)).toBe('chime');
  });
});

describe('a key renamed twice has a defined winner', () => {
  /**
   * `a` -> `b` -> `c` leaves TWO prefixed entries in `legacyKeys`, so two
   * different server rows resolve to one canonical key. A bulk read gives
   * no order guarantee and carries no timestamp, so before this the row
   * that happened to come later in the array silently won — the same
   * shape as the orphan bug this whole mechanism exists to fix, one
   * rename further along.
   *
   * Synthetic, on purpose: the registry has no double-rename today, and
   * inventing one just to test it would put a fake entry in front of
   * every other guard that walks DEFS.
   */
  const CHAIN = new Map<string, LegacyRow>([
    ['b', { canonical: 'c', rank: 0 }],   // the most recent former spelling
    ['a', { canonical: 'c', rank: 1 }],   // older still
  ]);

  it('prefers the most recent former spelling, whatever order they arrive in', () => {
    expect(pickLegacyRows(['a', 'b'], CHAIN)).toEqual(new Map([['c', 'b']]));
    expect(pickLegacyRows(['b', 'a'], CHAIN)).toEqual(new Map([['c', 'b']]));
  });

  it('takes the older row when it is the only one the account has', () => {
    expect(pickLegacyRows(['a'], CHAIN)).toEqual(new Map([['c', 'a']]));
  });

  it('ignores rows that answer to nothing', () => {
    expect(pickLegacyRows(['zzz'], CHAIN)).toEqual(new Map());
  });

  it('trips the moment a real key is renamed twice', () => {
    /*
     * Today every canonical key has at most ONE prefixed legacy row, so
     * `attachBackend`'s use of the precedence rule is behaviour-neutral:
     * adopting "the winner" and adopting "every legacy row" produce the
     * same result, and no test can tell them apart. That is a property
     * of the registry, not a hole in the guard — the rule itself is
     * pinned by the synthetic cases above.
     *
     * It stops being neutral the first time somebody renames an
     * already-renamed key. This fails on that day, so the end-to-end
     * path gets a real test while the person who created the case is
     * still holding it, instead of the divergence shipping unnoticed.
     */
    const byCanonical = new Map<string, string[]>();
    for (const { canonical, row } of RENAMED) {
      byCanonical.set(canonical, [...(byCanonical.get(canonical) ?? []), row]);
    }
    const doubled = [...byCanonical].filter(([, rows]) => rows.length > 1);
    expect(
      doubled.map(([c, rows]) => `${c} <- ${rows.join(', ')}`),
      'a key now has two former registry spellings, so which server row wins is '
      + 'live behaviour for the first time. Add an end-to-end attachBackend test '
      + 'for it, then update this one.',
    ).toEqual([]);
  });
});
