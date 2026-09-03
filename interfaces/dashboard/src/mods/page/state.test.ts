/**
 * What a tile says, tested on data — no rendering.
 *
 * The page's whole contribution over the card is STATE at a glance, so
 * the readers that compute it are the thing to pin. Each claim below is
 * one the page would get visibly wrong if the reader drifted: a touched
 * item showing Default, an off gate showing Changed, a category with no
 * single intensity inventing one.
 */
import { describe, it, expect } from 'vitest';
import { itemState, categoryTouched, categoryIntensity } from './state';
import { TAXONOMY, categoryById } from '../taxonomy';
import { MOD_DEFAULT, SIZE_DEFAULT, DEFS, type ModSetting } from '../../preferences';

const theme = (over: Partial<ModSetting> = {}): ModSetting => ({ ...MOD_DEFAULT, ...over });
/** A reader over a plain object, defaults from the registry beneath. */
const prefs = (over: Record<string, unknown> = {}) => (k: string) =>
  k in over ? over[k] : (DEFS as Record<string, { default: unknown }>)[k]?.default;

const item = (cat: string, id: string) => categoryById(cat as never)!.items.find((i) => i.id === id)!;

describe('itemState', () => {
  it('reads default when nothing it owns has moved', () => {
    expect(itemState(item('interface', 'corners'), theme(), prefs())).toBe('default');
  });

  it('reads changed when an axis it owns has moved', () => {
    expect(itemState(item('interface', 'corners'), theme({ radius: 'pill' }), prefs())).toBe('changed');
  });

  it('counts a kept axis as a change — Light is a choice even though a reset would not touch it', () => {
    expect(itemState(item('interface', 'theme'), theme({ mode: 'light' }), prefs())).toBe('changed');
  });

  it('does not blame one item for another item\'s axis', () => {
    // The corners tile must not light up because the colour changed.
    expect(itemState(item('interface', 'corners'), theme({ accent: 'purple' }), prefs())).toBe('default');
  });

  it('reads off for a gated item whose gate is down', () => {
    expect(itemState(item('sounds', 'keyboard'), theme(), prefs({ 'mods.sound.keyboard': false }))).toBe('off');
    expect(itemState(item('effects', 'ambient'), theme(), prefs())).toBe('off');
  });

  it('reads changed once the gate is up', () => {
    expect(itemState(item('sounds', 'keyboard'), theme(), prefs({ 'mods.sound.keyboard': true }))).toBe('changed');
  });

  it('reads changed for a non-gate preference that moved, even with the gate down', () => {
    // A person who picked a keyboard pack and then switched typing off
    // has still touched the item; Off is only for a gate at rest.
    const s = itemState(item('sounds', 'keyboard'), theme(),
      prefs({ 'mods.sound.keyboard': false, 'mods.sound.keyboard.pack': 'soft' }));
    expect(s).toBe('off');
    const s2 = itemState(item('sounds', 'keyboard'), theme(),
      prefs({ 'mods.sound.keyboard': true, 'mods.sound.keyboard.pack': 'soft' }));
    expect(s2).toBe('changed');
  });
});

describe('categoryTouched', () => {
  it('counts items, not axes', () => {
    const t = theme({ radius: 'pill', material: 'glass', accent: 'green' });
    expect(categoryTouched(categoryById('interface')!, t, prefs())).toEqual({ changed: 3, total: 5 });
  });

  it('is zero at defaults', () => {
    for (const cat of TAXONOMY)
      expect(categoryTouched(cat, theme(), prefs()).changed, cat.id).toBe(0);
  });
});

describe('categoryIntensity', () => {
  it('sounds is the volume', () => {
    expect(categoryIntensity(categoryById('sounds')!, theme(), SIZE_DEFAULT, prefs({ 'mods.sound.volume': 0.4 }))).toBe(40);
  });

  it('effects is the motion percentage, inverted like the panel', () => {
    expect(categoryIntensity(categoryById('effects')!, theme({ motion: 'snappy' }), SIZE_DEFAULT, prefs())).toBe(167);
    expect(categoryIntensity(categoryById('effects')!, theme({ motion: 'calm' }), SIZE_DEFAULT, prefs())).toBe(63);
  });

  it('size is the global multiplier', () => {
    expect(categoryIntensity(categoryById('size')!, theme(), { ...SIZE_DEFAULT, global: 1.25 }, prefs())).toBe(125);
  });

  it('interface has no single number, and says so with null rather than inventing one', () => {
    expect(categoryIntensity(categoryById('interface')!, theme({ accent: 'purple' }), SIZE_DEFAULT, prefs())).toBeNull();
  });
});
