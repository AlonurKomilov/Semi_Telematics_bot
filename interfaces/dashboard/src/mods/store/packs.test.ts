/**
 * A pack ships items, and every item belongs to exactly one pack.
 */
import { describe, it, expect } from 'vitest';
import { PACKS, packById, packOf, removable, type Pack } from './packs';
import { ITEM_AXES } from './items';
import { PUBLISHER } from './index';
import { AXIS_UI, defaultOf } from './axes';

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
