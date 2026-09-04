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
import { itemState, itemSummary, labelOf, categoryTouched, categoryIntensity } from './state';
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


describe('itemSummary — the tile answers instead of announcing', () => {
  it('names the value, using the label a pack already ships', () => {
    expect(itemSummary(item('interface', 'typeface'), theme({ font: 'mono' }), prefs())).toBe('Mono');
    expect(itemSummary(item('interface', 'theme'), theme({ accent: 'azure' }), prefs())).toContain('Azure');
  });

  it('title-cases an axis that has no label list anywhere', () => {
    // radius/material/icons are bare string enums — inventing a fifth
    // label list for four words would be worse than title-casing.
    expect(itemSummary(item('interface', 'corners'), theme({ radius: 'pill' }), prefs())).toBe('Pill');
    expect(itemSummary(item('interface', 'material'), theme({ material: 'glass' }), prefs())).toBe('Glass');
  });

  it('shows the mode alongside the accent, in the item\'s own axis order', () => {
    const sum = itemSummary(item('interface', 'theme'), theme({ mode: 'light', accent: 'green' }), prefs());
    expect(sum).toBe('Light · Green');
  });

  it('short-circuits to Off when the gate is down — a pack that cannot be heard', () => {
    const sum = itemSummary(item('sounds', 'keyboard'), theme(),
      prefs({ 'mods.sound.keyboard': false, 'mods.sound.keyboard.pack': 'soft' }));
    expect(sum, 'named a keyboard pack while typing makes no sound').toBe('Off');
  });

  it('names the pack once the gate is up, without saying On as well', () => {
    const sum = itemSummary(item('sounds', 'keyboard'), theme(),
      prefs({ 'mods.sound.keyboard': true, 'mods.sound.keyboard.pack': 'soft' }));
    expect(sum).toBe('Soft');
  });

  it('says On for a gate that has nothing else to show', () => {
    expect(itemSummary(item('effects', 'ambient'), theme(), prefs({ 'mods.ambient': true }))).toBe('On');
    expect(itemSummary(item('effects', 'ambient'), theme(), prefs())).toBe('Off');
  });

  it('reads the default as a value, not as blank — the tile always says something', () => {
    for (const cat of TAXONOMY)
      for (const it of cat.items) {
        const sum = itemSummary(it, theme(), prefs());
        // Size's items own no axis and no preference of their own; the
        // card beneath them is the whole control.
        if (cat.id === 'size') continue;
        expect(sum, `${cat.id}/${it.id} shows nothing at all`).toBeTruthy();
      }
  });
});


describe('labelOf prefers a pack\'s own label over title-casing its id', () => {
  /**
   * Every id shipping today title-cases to its own label, so against
   * the real lists this branch cannot be seen — a mutation that ignored
   * the labels entirely passed all 32 tests. The lists are a parameter
   * for exactly this reason.
   */
  const PACK = [[{ id: 'hi-contrast', label: 'High contrast' }]];

  it('uses the label when title-casing would get it wrong', () => {
    expect(labelOf('hi-contrast', PACK)).toBe('High contrast');
    // …and the fallback really is wrong for it, or the case proves nothing.
    expect(labelOf('hi-contrast', [])).toBe('Hi-contrast');
  });

  it('falls back to title case for an axis no list covers', () => {
    expect(labelOf('pill', PACK)).toBe('Pill');
  });

  it('reads booleans and numbers as a person would', () => {
    expect(labelOf(true)).toBe('On');
    expect(labelOf(false)).toBe('Off');
    expect(labelOf(0.4)).toBe('40%');
    expect(labelOf(undefined)).toBeNull();
  });
});
