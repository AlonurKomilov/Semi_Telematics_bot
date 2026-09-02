/**
 * "Reset" has to reach the legacy spelling too, or it resets nothing.
 *
 * `reset()` calls `removePref`, which removes only `4truck.pref.<key>`.
 * `readPref` then falls through to `readLegacy`, finds the old entry,
 * copies it forward and returns it — so the value is back on the next
 * read. The in-memory `values` map hides this: `store.get` serves the
 * default it was just handed, and the setting only springs back on the
 * next page load. The pre-paint script is worse — it reads the legacy
 * keys directly, so the FIRST PAINT after a reset is the old theme.
 *
 * Three keys carry a legacy spelling today (mods.theme has two), so this
 * is live for the whole mods surface, not a hypothetical.
 *
 * The tests assert at the STORAGE layer on purpose. Asserting through
 * `store.get` would pass on today's broken code, because that is exactly
 * the layer the bug hides behind.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { DEFS, MOD_DEFAULT } from './registry';
import { readPref, LS_PREFIX } from './local';
import { reset } from './store';

/** Every def that claims an older spelling, with the raw keys it claims. */
const WITH_LEGACY = Object.entries(DEFS)
  .map(([key, def]) => ({ key, legacy: (def as { legacyKeys?: string[] }).legacyKeys ?? [] }))
  .filter((e) => e.legacy.length > 0);

beforeEach(() => localStorage.clear());

describe('reset clears the legacy spelling as well as the canonical one', () => {
  it('has something to protect', () => {
    // A guard that walks an empty set passes for the wrong reason.
    expect(WITH_LEGACY.length).toBeGreaterThan(0);
  });

  it.each(WITH_LEGACY.map((e) => [e.key, e.legacy] as const))(
    '%s leaves no legacy entry behind',
    (key, legacy) => {
      // The VALUE does not matter here — a reset must clear the address
      // whether or not what sat there was valid.
      for (const raw of legacy) localStorage.setItem(raw, '"whatever"');
      localStorage.setItem(`${LS_PREFIX}${key}`, '"whatever"');

      reset(key);

      for (const raw of legacy) {
        expect(
          localStorage.getItem(raw),
          `${key}: "${raw}" survived the reset, so the next read migrates it forward again`,
        ).toBeNull();
      }
      expect(localStorage.getItem(`${LS_PREFIX}${key}`)).toBeNull();
    },
  );

  it('legacy keys are removed VERBATIM, prefixed or not', () => {
    // `readLegacy` reads them raw (local.ts), never through lsKey(), and
    // the chain deliberately mixes both forms: '4truck.pref.theme' carries
    // the prefix, 'dashboard-theme' predates it. A sweep that prefixed
    // them all would clear neither.
    const chain = DEFS['mods.theme'].legacyKeys ?? [];
    expect(chain.some((k) => k.startsWith(LS_PREFIX)), 'expected a prefixed entry').toBe(true);
    expect(chain.some((k) => !k.startsWith(LS_PREFIX)), 'expected a bare entry').toBe(true);
  });
});

describe('what the user actually sees', () => {
  it('a reset theme stays reset on the next page load', () => {
    // The reload path is `readPref`, not `store.get` — the in-memory map
    // is what hides this today.
    localStorage.setItem('dashboard-theme', JSON.stringify({ ...MOD_DEFAULT, radius: 'pill' }));
    expect((readPref('mods.theme') as { radius: string }).radius).toBe('pill');

    reset('mods.theme');

    expect(
      (readPref('mods.theme') as { radius: string }).radius,
      'the pre-reset value came back on the next read',
    ).toBe(MOD_DEFAULT.radius);
  });
});
