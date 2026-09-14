/**
 * A pack ships items, and every item belongs to exactly one pack.
 */
import { describe, it, expect } from 'vitest';
import { PACKS, packById, packOf, removable, type Pack } from './packs';
import { ITEM_AXES } from './items';
import { PUBLISHER } from './index';
import { AXIS_UI, defaultOf } from './axes';
import { MOD_FIELD_KIND, VALUE_FIELDS } from '../catalogue';

const every = ITEM_AXES.flatMap((a) => a.items.map((i) => ({ axis: a.axis, id: i.id })));

describe('every item belongs to exactly one pack', () => {
  it('there are items to account for', () => {
    expect(every.length, 'the store carries nothing').toBeGreaterThan(25);
  });

  it('no item is left without a pack — it could never be installed', () => {
    const orphans = every.filter((i) => !packOf(i.axis, i.id));
    expect(orphans.map((i) => `${i.axis}/${i.id}`),
      'an item no pack ships cannot arrive or leave').toEqual([]);
  });

  it('no item is claimed twice — it would leave once and still look present', () => {
    const twice = every.filter((i) => PACKS.filter((p) => (p.items[i.axis] ?? []).includes(i.id)).length > 1);
    expect(twice.map((i) => `${i.axis}/${i.id}`)).toEqual([]);
  });

  it('a pack ships nothing that does not exist', () => {
    for (const p of PACKS) {
      for (const [axis, ids] of Object.entries(p.items)) {
        expect(AXIS_UI[axis], `${p.id} ships for "${axis}", which is not an axis`).toBeTruthy();
        const real = ITEM_AXES.find((a) => a.axis === axis)?.items.map((i) => i.id) ?? [];
        for (const id of ids) {
          expect(real, `${p.id} ships ${axis}/${id}, which no axis carries`).toContain(id);
        }
      }
    }
  });
});

describe('the pack the app was drawn in', () => {
  const base = PACKS.filter((p) => p.base);

  it('there is exactly one, and it is the first thing on the page', () => {
    expect(base.length).toBe(1);
    expect(PACKS[0].base, 'the base pack is not first').toBe(true);
  });

  it('it cannot be taken off — it carries what every axis falls back to', () => {
    expect(removable(base[0].id)).toBe(false);
    for (const axis of Object.keys(AXIS_UI)) {
      const fallback = defaultOf(axis);
      if (!fallback) continue;
      expect(base[0].items[axis] ?? [],
        `${axis} falls back to "${fallback}", which the base pack does not ship`).toContain(fallback);
    }
  });

  it('a pack that is not the base one can be taken off', () => {
    expect(removable('not-a-pack')).toBe(true);
  });
});

describe('the house stamps the pack, as it stamps the item', () => {
  it('every pack carries the publisher', () => {
    for (const p of PACKS) expect(p.publisher, `${p.id} belongs to nobody`).toBe(PUBLISHER);
  });

  it('a pack is found by its id, and an unknown id finds nothing', () => {
    for (const p of PACKS) expect(packById(p.id)?.label).toBe(p.label);
    expect(packById('not-a-pack')).toBeUndefined();
  });

  it('id, label and description are fit to show and to store', () => {
    for (const p of PACKS as Pack[]) {
      expect(p.id).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(p.label.length).toBeGreaterThan(1);
      expect(p.description.length, `${p.id} says nothing about itself`).toBeGreaterThan(15);
    }
  });
});

describe('a pack brings items and only CHOOSES the system\'s values', () => {
  /** Every theme field some shelf is the home of. */
  const homed = Object.values(AXIS_UI)
    .flatMap((ui) => (ui.home.theme ? [...ui.home.theme] : []));

  it('there are both kinds, or this rule is guarding nothing', () => {
    expect(VALUE_FIELDS.length, 'nothing is the system\'s any more').toBeGreaterThan(3);
    expect(Object.values(MOD_FIELD_KIND).filter((k) => k === 'item').length)
      .toBeGreaterThan(5);
  });

  it('size stays the system\'s, by name', () => {
    // The owner's call, and the reason is measurable rather than a
    // matter of taste: size multiplies every dimension at once, and the
    // Size control starts at 100% because everything below it meets the
    // 24px hit-target floor. A pack that could SHIP a size would be able
    // to leave the app somewhere its own control cannot bring it back
    // from. Every other value field is arguable — corners most of all,
    // which is why this pins one name and not the list.
    expect(MOD_FIELD_KIND.size).toBe('value');
  });

  it('every shelf is the home of a field a pack may bring', () => {
    for (const field of homed) {
      expect(MOD_FIELD_KIND[field as keyof typeof MOD_FIELD_KIND],
        `a shelf sells "${field}", which is the system's to decide`).toBe('item');
    }
  });

  it('and nothing the system owns has a shelf to sell it on', () => {
    // `size` is the one that matters: it multiplies every dimension at
    // once, and the Size control starts at 100% because everything below
    // meets the 24px hit-target floor. A pack that shipped one could put
    // the app where its own control cannot bring it back from.
    //
    // Asked of FIELDS and the shelves that are their home, never of the
    // axis NAME: "icons" is an axis (which set of glyphs, an item) and
    // also a field (how heavy they are drawn, the system's). Two nouns,
    // one word, and the taxonomy is right to file them under one
    // heading — a person asking about icons means both.
    for (const field of VALUE_FIELDS) {
      expect(homed, `"${field}" is a shelf's home — a pack could ship one`)
        .not.toContain(field);
    }
  });

  it('every axis a pack ships on is one the store sells', () => {
    for (const p of PACKS) {
      for (const axis of Object.keys(p.items)) {
        const ui = AXIS_UI[axis];
        expect(ui, `${p.id} ships "${axis}", which is no shelf`).toBeTruthy();
        // Its home is a field a pack may bring — the `mods` shelf's home
        // is the preset identity, which is the pack itself.
        for (const field of ui.home.theme ?? []) {
          expect(MOD_FIELD_KIND[field as keyof typeof MOD_FIELD_KIND],
            `${p.id} ships on "${axis}", whose ${field} is the system's`).toBe('item');
        }
      }
    }
  });
});

/**
 * An item named after a pack belongs to that pack.
 *
 * `CLASSIC` is derived: it ships every item no themed pack claims. That
 * is the right default and it has one failure mode — a themed pack that
 * forgets an axis leaves an item WEARING ITS NAME sitting in the base
 * pack, where installing the pack does not bring it and removing the
 * pack does not take it away.
 *
 * It is not hypothetical. Night Haul shipped a wallpaper, a cue set, a
 * keyboard and a motion all called Night Haul, then gained an
 * interaction cue set of the same name that stayed in Classic — so
 * installing the pack would have given somebody its typing sound and
 * not its clicking one, on the same shelf page, with nothing to
 * explain the difference.
 *
 * A pack is still free to prepare an axis without shipping one; what it
 * may not do is leave its own namesake behind.
 */
describe('a themed pack keeps its namesakes', () => {
  const themed = PACKS.filter((p) => !p.base);

  it('finds packs to check', () => {
    expect(themed.length).toBeGreaterThan(2);
  });

  it('and no item wearing a pack name sits in the base pack', () => {
    const base = PACKS.find((p) => p.base);
    expect(base, 'no base pack — this checks nothing').toBeTruthy();
    const names = new Set(themed.map((p) => p.id));
    const stranded = Object.entries(base!.items).flatMap(
      ([axis, ids]) => ids.filter((id) => names.has(id)).map((id) => `${axis}/${id}`));
    expect(
      stranded,
      'this item wears a themed pack\'s name but ships with the base pack — '
        + 'installing that pack will not bring it and removing it will not take it '
        + 'away. Add the axis to the pack\'s `items`.',
    ).toEqual([]);
  });
});
