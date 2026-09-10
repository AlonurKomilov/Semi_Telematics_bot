/**
 * The rules a draggable divider has to keep to be worth having.
 *
 * It exists because both features hold the same tension and it changes
 * by TASK, not by person: auditing one truck wants a big card, hunting
 * across 190 wants a big list, and it is the same person an hour apart.
 * A setting cannot answer that — it lives two screens away.
 *
 * jsdom performs no layout, so a drag cannot be asserted by rendering:
 * the decisions are lexical and so is the guard, the same way the
 * panel's flex contract is held in layout.test.ts.
 */
import { describe, expect, it } from 'vitest';

import splitterSrc from './Splitter.tsx?raw';
import inventorySrc from '../features/inventory/InventoryPanel.tsx?raw';
import liveMapSrc from '../features/live-map/LiveMapPanel.tsx?raw';
import prefsSrc from '../prefs.ts?raw';
import { MIN_PCT, MAX_PCT } from './Splitter';

const src = splitterSrc as unknown as string;
const inventory = inventorySrc as unknown as string;
const liveMap = liveMapSrc as unknown as string;
const prefs = prefsSrc as unknown as string;

describe('the splitter', () => {
  it('leaves both regions on screen, whatever the drag', () => {
    // A card at 0 hides the truck just selected; a list at 0 hides the
    // way to select another.  Either leaves the panel looking broken
    // rather than configured.
    expect(MIN_PCT).toBeGreaterThanOrEqual(15);
    expect(MAX_PCT).toBeLessThanOrEqual(85);
    expect(src).toContain('Math.min(MAX_PCT, Math.max(MIN_PCT');
    // …and a stored number outside that range is refused on the way IN
    // too — in prefs, where it is read — or yesterday's value
    // reintroduces exactly what the clamp prevents.
    expect(prefs).toContain('n >= lo && n <= hi');
  });

  it('can be moved by somebody who cannot drag', () => {
    // The whole point of the ARIA role.  A resize that only a mouse can
    // perform is a resize most keyboard and switch users do not have.
    expect(src).toContain('role="separator"');
    expect(src).toContain('aria-valuenow');
    expect(src).toContain("e.key === 'ArrowUp'");
    expect(src).toContain("e.key === 'ArrowDown'");
    expect(src).toContain('tabIndex={0}');
  });

  it('undoes a bad drag without hunting for the pixel', () => {
    expect(src).toContain('onDoubleClick');
  });

  it('survives a drag that leaves the column', () => {
    // 320px is narrow; a fast drag exits it constantly.  Without pointer
    // capture the resize stops halfway and the person is left holding a
    // button that no longer does anything.
    expect(src).toContain('setPointerCapture');
    // …and touch is a drag, not a scroll.
    expect(src).toContain("touchAction: 'none'");
  });

  it('writes on release, not sixty times a second', () => {
    // A drag is ONE decision.  Storage does not need to hear it per
    // frame, and a panel that writes per frame is a panel that stutters.
    expect(src).toContain('apply(((e.clientY - box.top) / box.height) * 100, false)');
  });

  it('is a real target, not a hairline', () => {
    // An edge handle is the case WCAG's spacing exception rarely saves,
    // and 2px of line is not something anybody can aim at.
    expect(src).toContain('height: 10');
  });
});

describe('each surface keeps its own ratio', () => {
  it('does not make one panel fight the other', () => {
    // The map/list ratio and the card/list ratio are different
    // judgements about different regions.  One shared number would mean
    // resizing Inventory silently resized Live Map.
    expect(inventory).toContain('storageKey="inventoryCardPct"');
    expect(liveMap).toContain('storageKey="liveMapMapPct"');
  });

  it('replaces a constant rather than adding a control', () => {
    // The card was a hardcoded 70%; the list a hardcoded 240px basis.
    // Neither number was ever the reader's.
    expect(inventory).toContain('maxHeight: `${cardPct}%`');
    expect(inventory).not.toContain("maxHeight: '70%'");
    expect(liveMap).toContain('`0 1 ${100 - mapPct}%`');
    expect(liveMap).not.toContain("'0 1 240px'");
  });

  it('is absent where there is nothing to divide', () => {
    // One region has no seam: no vehicle chosen means no card, and a
    // folded list is a header bar.
    // Inventory carries a SECOND condition: the card is capped with
    // `maxHeight`, so a short card renders at its content height and a
    // drag moves nothing.  The handle is not offered when it could not
    // act — a control that cannot act is worse than one that is absent.
    expect(inventory).toMatch(/\{selected && cardCanResize && \(\s*\n\s*<Splitter/);
    expect(inventory).toContain('card.scrollHeight > floor + 1');
    expect(liveMap).toMatch(/\{listOpen && \(\s*\n\s*<Splitter/);
  });
});

describe('the direction the line moves', () => {
  it('reports the share ABOVE it, always', () => {
    // The first draft had Live Map store the share of the region BELOW
    // (the list) while feeding it the pointer's distance from the TOP:
    // dragging down made the list bigger, which pushed the line UP.  The
    // owner felt it immediately — "pasga qilsam tepaga".
    //
    // One rule now, so no caller can get it backwards: a Splitter always
    // reports what is above it, and a caller whose region is the lower
    // one subtracts, where the reader can see the subtraction.
    expect(src).toContain('apply(((e.clientY - box.top) / box.height) * 100, false)');
    expect(liveMap).toContain('${100 - mapPct}');
    expect(liveMap).not.toContain('setListPct');
  });

  it('tells the map its box changed', () => {
    // Leaflet does not notice its container being resized.  Without
    // this the drag moved the box and the map went on rendering for the
    // old one — frozen tiles, the truck off centre.
    expect(liveMap).toContain('[listOpen, cardShown, cardOpen, mapPct]');
    expect(liveMap).toContain('invalidateSize()');
  });

  it('answers the cursor, so a 2px line is not mistaken for a border', () => {
    // Only the CLASS is checkable from here: this project's Vite config
    // processes CSS, so `?raw` on index.css hands a test an empty string
    // (probed, not assumed).  The other half of the contract — that the
    // class has a rule — is held in the repo-root suite, which can read
    // both files: tests/test_extension_splitter_css.py.
    expect(src).toContain('className="splitter"');
    expect(src).toContain('<i aria-hidden />');
  });
});
