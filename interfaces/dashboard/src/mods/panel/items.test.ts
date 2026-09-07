/**
 * One item, one page — held in both directions.
 *
 * `ITEM_GROUPS` is the third hand-written statement of what the
 * taxonomy contains, after the taxonomy itself and the panel that
 * composes it. A third copy drifts the way the first two did, and it
 * drifts in the one way the /mods page cannot see: a tile with no
 * entry renders "There is no …" for a thing that exists, and an entry
 * with no tile is a page nobody can reach.
 */
import { describe, it, expect } from 'vitest';
import { ITEM_GROUPS, CATEGORY_CONTROLS } from './items';
import { TAXONOMY, browsableItemsOf, categoryById } from '../taxonomy';

/** Every address the page will ask the map for. */
const paged = TAXONOMY
  .filter((c) => c.panel)
  .flatMap((c) => browsableItemsOf(c.id).map((i) => `${c.id}/${i.id}`));

describe('every browsable item has a page, and every page an item', () => {
  it('has items to check', () => {
    // Both lists below could be empty and "equal", which is the failure
    // mode a totality check has to rule out first.
    expect(paged.length).toBeGreaterThan(8);
  });

  it('a tile never opens on "There is no …"', () => {
    const missing = paged.filter((k) => !(k in ITEM_GROUPS));
    expect(missing, 'browsable items with no page — the tile opens on nothing').toEqual([]);
  });

  it('and no page is unreachable', () => {
    const orphans = Object.keys(ITEM_GROUPS).filter((k) => !paged.includes(k));
    expect(orphans, 'pages no tile leads to').toEqual([]);
  });

  /** Size is the declared exception: `panel: false`, one card, and the
   *  page renders `SizeCard` for either of its addresses. Its absence
   *  here is the rule, and this is the assertion that keeps it one. */
  it('size is absent on purpose, and still panel: false', () => {
    expect(Object.keys(ITEM_GROUPS).some((k) => k.startsWith('size/'))).toBe(false);
    expect(categoryById('size')?.panel, 'Size joined the panel — it needs pages now').toBe(false);
  });

  it('every mapped page is a component', () => {
    for (const [k, C] of Object.entries(ITEM_GROUPS))
      expect(C, `${k} maps to nothing renderable`).toBeTypeOf('function');
  });
});

describe('a category-level control belongs to a real category', () => {
  it('names only categories the taxonomy has', () => {
    for (const k of Object.keys(CATEGORY_CONTROLS))
      expect(categoryById(k), `${k} is not a category`).toBeDefined();
  });

  /** The pack chips sit beside the volume on the category page, and the
   *  taxonomy has to agree that the pack is the category's — it used to
   *  file `mods.sound.pack` under "Interface sounds", which the page
   *  split made visibly wrong. Two lanes read that key. */
  it('sounds owns the pack as well as the level', () => {
    expect(categoryById('sounds')?.prefs).toContain('mods.sound.volume');
    expect(categoryById('sounds')?.prefs).toContain('mods.sound.pack');
    for (const i of categoryById('sounds')!.items)
      expect(i.prefs ?? [], `${i.id} claims the shared pack`).not.toContain('mods.sound.pack');
  });
});
