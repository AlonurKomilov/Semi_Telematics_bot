/**
 * Nothing in the POI panel puts white on a layer's colour.
 *
 * It did, in four places, copied from one to the next: the tick inside
 * the filled checkbox, the count badge, the "N active" badge and the
 * selected brand chip.  Measured, white on the amber fuel colour is
 * 2.15:1 and on the cyan rest-area colour 2.43:1 — against 4.5:1 for
 * text and 3:1 for a mark that identifies a control.  Seven of the
 * eight layers failed; the eighth is blue, which is why it shipped.
 */
import { describe, expect, it } from 'vitest';

import { POI_LAYERS } from './layers';
import { contrastRatio, parseHex, readableTextOn } from '@/mods';
import panelSrc from './PoiLayerPanel.tsx?raw';

const panel = panelSrc as unknown as string;

/** Comments explain the rule; they are not the rule — and this file's
 *  own comments name the class it forbids. */
function code(src: string): string {
  return src.replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\s\/\/.*$/, '')).join('\n');
}

const ratio = (a: string, b: string) => {
  const [x, y] = [parseHex(a), parseHex(b)];
  return x && y ? contrastRatio(x, y) : 0;
};

describe('the ink on a layer colour is chosen, not assumed', () => {
  it('clears 3:1 on every layer — the tick inside the filled box', () => {
    // WCAG 1.4.11: a graphic that identifies a control's state.
    for (const def of POI_LAYERS) {
      expect(ratio(readableTextOn(def.color), def.color), `${def.label} ${def.color}`)
        .toBeGreaterThanOrEqual(3);
    }
  });

  it('clears 4.5:1 on every layer that actually has brand chips', () => {
    // The chip carries TEXT on the layer's colour.  Only fuel and DEF
    // have chips, and both clear it; the violet weigh station that
    // cannot reach 4.5 against any ink has none.
    const withChips = POI_LAYERS.filter((d) => d.brandFilters?.length);
    expect(withChips.length).toBeGreaterThan(0);
    for (const def of withChips) {
      expect(ratio(readableTextOn(def.color), def.color), `${def.label} ${def.color}`)
        .toBeGreaterThanOrEqual(4.5);
    }
  });

  it('clears 4.5:1 on every layer, chips or not — the count pill', () => {
    // The pill sits on the colour too, so the text floor applies to all
    // eight.  The tightest is the violet weigh station at 4.68:1 — it
    // passes only because the dark ink is #0a0a0a; against a lighter
    // near-black (#0f1115) the same colour measures 4.46 and fails.
    // That is why the ink is a shared constant and not a local guess.
    for (const def of POI_LAYERS) {
      expect(ratio(readableTextOn(def.color), def.color), `${def.label} ${def.color}`)
        .toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe('the panel no longer paints white on a layer colour', () => {
  it('has no text-white left in it', () => {
    expect(code(panel)).not.toContain('text-white');
  });

  it('every colour it does paint on gets a chosen ink', () => {
    // Four places take the layer's colour as a ground: the tick, the
    // count pill, the "N active" badge and the selected brand chip.
    // Each must pair it with `readableTextOn`, never a fixed colour.
    const src = code(panel);
    const grounds = (src.match(/background: def\.color/g) ?? []).length;
    const inks = (src.match(/readableTextOn\(def\.color\)/g) ?? []).length;
    expect(grounds).toBeGreaterThan(0);
    expect(inks, 'every layer-coloured ground needs its ink chosen')
      .toBeGreaterThanOrEqual(grounds);
  });
});
