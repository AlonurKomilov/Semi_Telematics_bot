/**
 * The taxonomy is TOTAL over the settings it claims to describe.
 *
 * `taxonomy.ts` exists because the category shape was written out by
 * hand in six places that stopped agreeing. Deriving them fixed the
 * disagreement; it does not stop the taxonomy itself from falling
 * behind the stored shape. A field added to `ModSetting` and never
 * placed under an item is a setting with no category, no reset, no tile
 * on /mods and no heading in the panel — and nothing would say so.
 *
 * So this reads the interface off disk and forces a decision on every
 * field: an item owns it, or this file names it and says why.
 *
 * The interface is parsed rather than imported because TypeScript types
 * do not exist at runtime — and parsing the TYPE rather than
 * `MOD_DEFAULT` is deliberate: `brand` has no default (its default is
 * absence), so a guard over the defaults would have missed exactly the
 * field that was hardest to place.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { TAXONOMY, MOD_FIELD_CATEGORY, PANEL_SECTIONS, resetAxesOf, headingsOf } from './taxonomy';

const REGISTRY = readFileSync(join(__dirname, '..', 'preferences', 'registry.ts'), 'utf8');

/** The field names of one interface, in declaration order. */
function fieldsOf(name: string): string[] {
  const at = REGISTRY.indexOf(`export interface ${name} {`);
  if (at < 0) throw new Error(`no interface ${name} in registry.ts — this reader is stale`);
  const end = REGISTRY.indexOf('\n}', at);
  const body = REGISTRY.slice(at, end);
  // `field:` or `field?:` at one indent level, ignoring comment bodies.
  return [...body.matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1]);
}

/**
 * Fields a person does not set, each with the reason it is absent.
 *
 * Adding to this list is a DECISION. It is not a place to park a field
 * somebody has not got round to categorising — every entry here says
 * why the field is not a thing anyone chooses.
 */
const NOT_AN_ITEM: Record<string, string> = {
  color: 'the deprecated mode+accent alias, re-derived on every write — never read to decide anything',
  mod: 'the identity of an installed look, not an axis it carries; the container row owns it',
  tokens: 'a raw token override a mod may install; it has no control and no producer today',
};

describe('the taxonomy covers the stored shape', () => {
  const fields = fieldsOf('ModSetting');

  it('reads the interface it is checking', () => {
    // A parser that found nothing would make every assertion below pass.
    expect(fields.length, 'no fields parsed out of ModSetting').toBeGreaterThan(8);
    expect(fields).toContain('accent');
    expect(fields).toContain('brand');
  });

  const owned = new Set(TAXONOMY.flatMap((c) => c.items.flatMap((i) => i.axes)));

  it('places every field under an item, or names why not', () => {
    const unplaced = fields.filter((f) => !owned.has(f) && !(f in NOT_AN_ITEM));
    expect(
      unplaced,
      'a field of ModSetting belongs to no taxonomy item and is not declared as '
        + 'something a person does not set. It would have no category, no reset, '
        + 'no tile on /mods and no heading in the panel — add it to an item in '
        + 'taxonomy.ts, or to NOT_AN_ITEM here with the reason.',
    ).toEqual([]);
  });

  it('does not claim an axis the stored shape does not have', () => {
    const phantom = [...owned].filter((a) => !fields.includes(a));
    expect(phantom, 'an item names an axis ModSetting does not declare').toEqual([]);
  });

  it('never places one axis under two items', () => {
    const all = TAXONOMY.flatMap((c) => c.items.flatMap((i) => i.axes));
    const twice = all.filter((a, i) => all.indexOf(a) !== i);
    // A shared axis would be reset by two sections, and the later spread
    // in RESET_AXES would silently decide which default won.
    expect(twice, 'an axis belongs to two items').toEqual([]);
  });

  it('keeps NOT_AN_ITEM honest — every excuse names a field that exists', () => {
    const stale = Object.keys(NOT_AN_ITEM).filter((f) => !fields.includes(f));
    expect(stale, 'an excuse outlived the field it excused').toEqual([]);
  });
});

describe('the size settings are covered too', () => {
  it('names the Size category and its own preference key', () => {
    const size = TAXONOMY.find((c) => c.id === 'size');
    expect(size, 'no Size category').toBeDefined();
    expect(size!.prefs, 'Size does not name the key it stores in').toContain('mods.size');
    // Size is deliberately NOT a panel section — it is a card of its own.
    expect(size!.panel).toBe(false);
    expect(PANEL_SECTIONS).not.toContain('size');
  });
});

describe('the derivations stay usable', () => {
  it('every Mod field lands in a category the card renders or Size', () => {
    for (const [field, cat] of Object.entries(MOD_FIELD_CATEGORY))
      expect(TAXONOMY.map((c) => c.id), `"${field}" lands in "${cat}"`).toContain(cat);
  });

  it('every panel section has at least one heading, or it renders an empty box', () => {
    for (const s of PANEL_SECTIONS)
      expect(headingsOf(s).length, `section "${s}" renders no group at all`).toBeGreaterThan(0);
  });

  it('the two reset partitions are non-empty and disjoint', () => {
    const i = resetAxesOf('interface');
    const e = resetAxesOf('effects');
    expect(i.length).toBeGreaterThan(0);
    expect(e.length).toBeGreaterThan(0);
    expect(i.filter((a) => e.includes(a)), 'an axis resets in two sections').toEqual([]);
  });
});
