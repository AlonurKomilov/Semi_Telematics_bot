/**
 * The panel's structural contracts, read from the source — the flex
 * column, and the one grant it must not assume it has.
 *
 * jsdom performs no layout, so none of this can be asserted by
 * rendering: a squeezed card and a healthy one have identical DOM.  The
 * decisions are lexical, and so is the guard.
 *
 * What went wrong once, and what these hold:
 *
 * The panel is a flex COLUMN.  The truck list's flex-basis was `auto` —
 * its content — and with 190 vehicles that overflows the column by
 * thousands of pixels before anything else is considered.  Flex then
 * distributes the deficit across every item that may shrink.  The
 * selected-vehicle card had just been given `minHeight: 0` (so a
 * `maxHeight` ceiling could work) which removed its automatic minimum
 * size, and it was squeezed to a scrolling sliver showing one line and a
 * Close button.
 *
 * Two rules came out of it, and either alone is enough to prevent it:
 * the fixed regions do not shrink, and the elastic one asks for what is
 * LEFT rather than for what it contains.
 */
import { describe, expect, it } from 'vitest';

// Imported as text, not read from disk: the tests run in jsdom, where
// `import.meta.url` is an http URL and node's file helpers refuse it.
import panelSrc from './InventoryPanel.tsx?raw';

const src = panelSrc as unknown as string;

describe('the Inventory panel holds its shape', () => {
  it('lets the elastic region ask for what is left, not for its content', () => {
    // `1 1 0`, never `1 1 auto`: from a content basis the column is
    // already overflowing when the layout begins, and every other region
    // pays for it.
    expect(src).toContain("flex: '1 1 0'");
    expect(src).not.toContain("flex: '1 1 auto'");
  });

  it('refuses to shrink the two fixed regions', () => {
    // Named, not counted: `flexShrink: 0` appears on row parts too, and
    // a count would go red for a reason that has nothing to do with the
    // column.  These are the two REGIONS of it.
    const card = src.slice(src.indexOf('<div className="sheet"'));
    expect(card.slice(0, card.indexOf('>')), 'the selected-vehicle card').toContain('flexShrink: 0');

    const i = src.indexOf("placeholder=\"Search vehicles");
    const searchOpen = src.lastIndexOf('<div style={{', i);
    expect(src.slice(searchOpen, i), 'the search block').toContain('flexShrink: 0');
  });

  it('keeps the card capped and scrolling rather than unbounded', () => {
    // The ceiling is the other half: measured at 320px with the add form
    // open and a full item list, the card alone reaches ~615px, and
    // neither it nor the panel root scrolls without this.
    const card = src.slice(src.indexOf('<div className="sheet"'));
    expect(card).toContain("maxHeight: '70%'");
    expect(card).toContain("overflowY: 'auto'");
    // …and NOT minHeight:0, which is what removed its automatic minimum
    // and let the list crush it.
    expect(card.slice(0, card.indexOf('>'))).not.toContain('minHeight: 0');
  });
});

describe('the panel does not assume a grant it was split away from', () => {
  it('moves Google\u2019s map only for somebody who may see positions', () => {
    // A position is a LOCATION read whoever asks for it.  Inventory was
    // split out of Vehicles precisely so it could be granted to a person
    // with no business seeing where the trucks are — so `positionOf` is
    // not called unless /extension/me said this person may open Live Map.
    expect(src).toContain("features.includes('live-map')");
    expect(src).toMatch(/if \(canLocate && follow\)/);
    // The gate stands BEFORE the call, not beside it.
    const before = src.slice(0, src.indexOf('positionOf('));
    expect(src.indexOf('positionOf(')).toBeGreaterThan(-1);
    expect(before).toContain('canLocate');
    // …and the READ of the preference is gated too: an ungranted panel
    // does not even ask what the setting is.
    expect(src).toMatch(/if \(!canLocate\) return;/);
  });

  it('reads the follow preference but never offers a second switch', () => {
    // It is a preference of the PANEL — Settings owns it.  This used to
    // render its own copy, which made three switches for one stored
    // value.  The rule is enforced across all three files in
    // src/prefs.test.ts; this line keeps THIS panel honest where its
    // other layout rules live.
    expect(src).toContain("getFollowPref");
    expect(src).not.toContain('setFollowPref');
  });

  it('keeps the position read in the location feature, not in this one', () => {
    // It lives in features/live-map/locate.ts: the route it uses is
    // gated on can_view_location, and putting it under features/
    // inventory/ would file a location read under the feature that must
    // not need one.
    expect(src).toContain("from '../live-map/locate'");
  });
});
