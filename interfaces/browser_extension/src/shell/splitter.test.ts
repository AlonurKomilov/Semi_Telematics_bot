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
import { MIN_PCT, MAX_PCT, rangeFor } from './splitterRange';

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
    // With no pixel floors declared those two ARE the range, and a drag
    // past either end lands on it rather than through it.
    expect(rangeFor(600)).toEqual({ lo: MIN_PCT, hi: MAX_PCT });
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
    expect(liveMap).toContain('`0 1 ${mapPct}%`');
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
    // The share ABOVE the line is sized DIRECTLY — no subtraction anywhere,
    // which is the form that cannot be got backwards.  Sizing the region
    // BELOW instead left the line floating off the cursor by exactly the
    // vehicle card's height, because the card also sits above the line.
    expect(liveMap).toContain('`0 1 ${mapPct}%`');
    expect(liveMap).toContain("flex: listOpen ? '1 1 0' : '0 0 auto'");
    // …and a FOLDED list gives its room to the map rather than to nothing:
    // with `0 1 …%` on the map and `0 0 auto` on the folded list, no child
    // had flex-grow and the leftover simply went blank.
    expect(liveMap).toContain("flex: listOpen ? `0 1 ${mapPct}%` : '1 1 auto'");
    expect(liveMap).not.toContain('setListPct');
    expect(liveMap).not.toContain('100 - mapPct');
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

describe('the session ends the same way however it ends', () => {
  it('has ONE reset, reached by all three paths', async () => {
    // It lived only in `disconnect()`, so a 401 or a token revoked from
    // another device left the previous person's abilities and their
    // cached inventory and positions in memory for whoever came next.
    const app = (await import('./App.tsx?raw')).default as unknown as string;
    expect(app).toContain('const resetSession = ({ keepView = false } = {}) => {');
    // An aged-out token is usually the SAME person mid-task, so the two
    // involuntary paths keep the screen they were on; only the deliberate
    // Disconnect returns the panel to where a first run starts.
    expect(app).toContain('resetSession({ keepView: true })');
    expect(app).toContain('if (!keepView) setView(');
    // Three CALL SITES — the 401, the token leaving storage, and the
    // deliberate Disconnect — counted whatever arguments each passes.
    // The declaration reads `resetSession = (`, so it does not match; every
    // hit here is a real call site, whatever arguments it passes.
    const calls = (app.match(/resetSession\(/g) || []).length;
    expect(calls).toBeGreaterThanOrEqual(3);
    expect(app).toContain('forgetInventory();');
    expect(app).toContain('setAbilities([]);');
  });

  it('lets Settings hear a pref written outside it', async () => {
    // The overlay switch on google.com/maps is a second writer; Settings
    // read once and showed the old answer to its own question.
    const st = (await import('./Settings.tsx?raw')).default as unknown as string;
    expect(st).toContain('chrome.storage.onChanged.addListener(onChange)');
    expect(st).toContain('OVERLAY_PREF_KEY in c');
    expect(st).toContain('FOLLOW_KEY in c');
  });

  it('derives “waiting” from the pending connection, not from mounting', async () => {
    const c = (await import('./Connect.tsx?raw')).default as unknown as string;
    expect(c).toContain('void getPending().then');
    expect(c).toContain('p.expires > Date.now()');
  });
});

describe('the range it offers is the range the layout will honour', () => {
  // A splitter speaks per cent; the regions have floors in PIXELS.  On a
  // 600px column 20% is 120px, so Live Map's old 220px map floor refused
  // the minimum the separator was advertising — Home stopped short, the
  // drag stopped short, and the shortfall was taken out of the list,
  // whose last rows fell off a panel with nothing to scroll them back.
  it('raises the minimum until the region above can pay its floor', () => {
    // 120px of a 600px column is exactly 20%, so nothing moves…
    expect(rangeFor(600, 120).lo).toBe(MIN_PCT);
    // …and on a shorter column the same floor costs more of it.
    expect(rangeFor(400, 120).lo).toBe(30);
    // The old number, at the height that showed the bug.
    expect(rangeFor(600, 220).lo).toBe(37);
  });

  it('lowers the maximum until the region below can pay its floor', () => {
    expect(rangeFor(600, undefined, 193).hi).toBe(68);
    expect(rangeFor(1000, undefined, 193).hi).toBe(MAX_PCT);
  });

  it('never proposes a minimum above its own maximum', () => {
    // A column too short to pay both floors.  The region BELOW wins: a
    // small map still works, while a list without its header and first
    // row leaves no way to pick another vehicle at all.
    const r = rangeFor(250, 120, 193);
    expect(r.lo).toBeLessThanOrEqual(r.hi);
    expect(r.hi).toBe(23);
    expect(r.lo).toBe(23);
  });

  it('falls back to the fixed range before the column has been measured', () => {
    // A ResizeObserver reports after the first paint; until then height
    // is 0, and dividing by it would hand the layout a NaN.
    expect(rangeFor(0, 120, 193)).toEqual({ lo: MIN_PCT, hi: MAX_PCT });
  });

  it('quotes that range to the keyboard and to assistive tech, not the constants', () => {
    expect(src).toContain('aria-valuemin={lo}');
    expect(src).toContain('aria-valuemax={hi}');
    expect(src).toContain("e.key === 'Home') { apply(lo, true)");
    expect(src).toContain("e.key === 'End') { apply(hi, true)");
    expect(src).toContain('Math.min(hi, Math.max(lo, Math.round(next)))');
  });

  it('is told its floors by the surface that imposes them', () => {
    // Two numbers in two files drift.  Live Map names each floor once,
    // beside the element that imposes it, and hands them over.
    expect(liveMap).toContain('minAbovePx={MAP_FLOOR_PX} minBelowPx={BELOW_FLOOR_PX}');
    expect(liveMap).toContain('minHeight: MAP_FLOOR_PX');
    expect(liveMap).toContain('minHeight: LIST_FLOOR_PX');
    expect(liveMap).not.toContain('minHeight: 220');
  });
});
