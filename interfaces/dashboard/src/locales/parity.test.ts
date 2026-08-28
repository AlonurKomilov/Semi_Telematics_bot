/**
 * Locale parity — the drift nobody could see.
 *
 * `i18n.ts` sets `fallbackLng: 'en'`, which is the right behaviour and
 * also the reason this file has to exist: a key missing from a
 * translation renders the English string instead of throwing, so a
 * locale can fall behind for months without one test, one build or one
 * user report noticing. Before this guard, exactly one test in the repo
 * read this directory at all, and it read `en.json` only.
 *
 * What accumulated in that silence: 272 keys absent from all eight
 * translations, and `uz.json` — the owner's own language — half English.
 *
 * Two rules, and neither of them is "translate everything":
 *
 *   1. A locale may not fall FURTHER behind. The counts below are a
 *      ratchet, not a target. Adding an English key without its
 *      translations is what produced the backlog, and this is the line
 *      that makes that a build failure instead of a silent fallback.
 *
 *   2. A locale may not carry a key English does not have. That string
 *      can never render — the lookup goes through `en.json`'s keyspace —
 *      so it is either a typo, or a translation left behind by a rename.
 *      Both are invisible without this check: the file is valid JSON and
 *      the app is happy.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const DIR = __dirname;

type Tree = { [k: string]: string | Tree };

function flatten(o: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(o)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') out[key] = v;
    else Object.assign(out, flatten(v, key));
  }
  return out;
}

const load = (f: string) =>
  flatten(JSON.parse(readFileSync(join(DIR, f), 'utf8')) as Tree);

const EN = load('en.json');
const LOCALES = readdirSync(DIR)
  .filter((f) => f.endsWith('.json') && f !== 'en.json')
  .map((f) => ({ name: f.replace('.json', ''), keys: load(f) }));

/**
 * How far behind each locale was when this guard landed. These are
 * CEILINGS. Lower one when you translate; never raise one — a raise is
 * the exact event this file exists to catch.
 */
const BEHIND: Record<string, number> = {
  // am/pa/so dropped by one when `theme.color_light` was retired: it was
  // a duplicate of `theme.light`, which every locale already has, and
  // English losing a key those three never carried moves them a step
  // closer for free. A ratchet down, which is the only direction allowed.
  am: 491,
  es: 272,
  fr: 272,
  pa: 491,
  ru: 272,
  so: 491,
  uk: 424,
  uz: 630,
};

/**
 * Keys these translations carry that English does not, found by this
 * guard on its first run. Both are renames that only got applied on one
 * side:
 *   · `work_orders_page.card_draft` — the Draft card was removed from
 *     the page; four translations still describe it.
 *   · `nav.geozones` — English calls it `nav.geofences`. So uz has a
 *     translation for that nav item AND shows the English word, because
 *     the two halves never met. (Feature names stay English by owner
 *     decision, so the fix is to delete the orphan, not re-point it.)
 *
 * Not fixed here: every one of these files is open in another
 * developer's editor right now.
 */
const ORPHAN_DEBT: Record<string, string[]> = {
  es: ['work_orders_page.card_draft'],
  fr: ['work_orders_page.card_draft'],
  ru: ['work_orders_page.card_draft'],
  uk: ['work_orders_page.card_draft'],
  uz: ['nav.geozones'],
};

const missingIn = (keys: Record<string, string>) =>
  Object.keys(EN).filter((k) => !(k in keys));
const orphansIn = (keys: Record<string, string>) =>
  Object.keys(keys).filter((k) => !(k in EN));

describe('locale parity', () => {
  it('finds the locale files at all', () => {
    // If this ever reads zero, every other test below passes vacuously.
    expect(LOCALES.map((l) => l.name)).toEqual(
      ['am', 'es', 'fr', 'pa', 'ru', 'so', 'uk', 'uz'],
    );
    expect(Object.keys(EN).length).toBeGreaterThan(1000);
  });

  it('lets no locale fall further behind English', () => {
    const worse = LOCALES
      .map((l) => ({ name: l.name, now: missingIn(l.keys).length }))
      .filter((l) => l.now > (BEHIND[l.name] ?? 0))
      .map((l) => `${l.name}: ${l.now} missing, ceiling ${BEHIND[l.name]}`);
    expect(
      worse,
      'a key was added to en.json without its translations — add them, or ' +
        'accept that this string renders in English for these users',
    ).toEqual([]);
  });

  it('never carries a key English does not have', () => {
    const orphans = LOCALES.flatMap((l) =>
      orphansIn(l.keys)
        .filter((k) => !(ORPHAN_DEBT[l.name] ?? []).includes(k))
        .map((k) => `${l.name}: ${k}`),
    );
    expect(
      orphans,
      'this string can never render — the lookup goes through en.json. ' +
        'Either the key is a typo, or English renamed it and the ' +
        'translation was left behind',
    ).toEqual([]);
  });

  it('keeps the ceilings and the orphan list honest', () => {
    // A ceiling that is far above the truth stops being a ratchet, and a
    // named orphan that is no longer an orphan hides the next real one.
    const stale: string[] = [];
    for (const { name, keys } of LOCALES) {
      const now = missingIn(keys).length;
      if (now < (BEHIND[name] ?? 0)) {
        stale.push(`BEHIND.${name} says ${BEHIND[name]}, truth is ${now} — lower it`);
      }
      const live = new Set(orphansIn(keys));
      for (const k of ORPHAN_DEBT[name] ?? []) {
        if (!live.has(k)) stale.push(`ORPHAN_DEBT.${name}: ${k} is gone — delete the entry`);
      }
    }
    expect(stale).toEqual([]);
  });
});
