/**
 * The panel's preferences, and the rule that there is ONE door to each.
 *
 * "Follow in Google Maps" was rendered in three places for one stored
 * value — Settings, the Live Map and Inventory — each with its own copy
 * of the first-time notice. A preference offered in every feature that
 * happens to use it stops reading as a property of the panel and starts
 * reading as a property of the screen you are on, and the person has to
 * wonder whether the switch in front of them is the same switch.
 *
 * It also sat in `features/live-map/`, so the SHELL imported from a
 * feature to read it and Inventory imported from live-map for a setting
 * about neither.
 */
import { describe, expect, it } from 'vitest';

import { getFollowPref, setFollowPref, wasFollowWarned, markFollowWarned } from './prefs';
import settingsSrc from './shell/Settings.tsx?raw';
import liveMapSrc from './features/live-map/LiveMapPanel.tsx?raw';
import inventorySrc from './features/inventory/InventoryPanel.tsx?raw';
import googleMapsSrc from './features/live-map/googleMaps.ts?raw';

const settings = settingsSrc as unknown as string;
const liveMap = liveMapSrc as unknown as string;
const inventory = inventorySrc as unknown as string;
const googleMaps = googleMapsSrc as unknown as string;

describe('the follow preference', () => {
  it('is OFF until somebody switches it on', async () => {
    // Following REPLACES what is open in the person's Google Maps tab,
    // and a setting that can throw away a route they were planning is
    // not one to switch on for them.
    expect(await getFollowPref()).toBe(false);
    await setFollowPref(true);
    expect(await getFollowPref()).toBe(true);
  });

  it('remembers that the consequence has been explained once', async () => {
    expect(await wasFollowWarned()).toBe(false);
    await markFollowWarned();
    expect(await wasFollowWarned()).toBe(true);
  });
});

describe('one preference, one door', () => {
  it('is changed in Settings and nowhere else in the panel', () => {
    // The WRITE is the door.  Reading it is what a feature does to
    // behave correctly; writing it is what makes a second switch.
    expect(settings).toContain('setFollowPref');
    for (const [name, src] of [['LiveMapPanel', liveMap], ['InventoryPanel', inventory]] as const) {
      expect(src, `${name} must not write the preference`).not.toContain('setFollowPref');
      expect(src, `${name} must not render a second switch`).not.toContain('Follow in Google Maps<');
    }
  });

  it('is explained where it is changed', () => {
    // The first-time notice used to fire inside whichever panel you
    // happened to toggle from — the warning about replacing your tab
    // lived two screens from the setting that does it.
    expect(settings).toContain('FOLLOW_WARNING');
    expect(liveMap).not.toContain('markFollowWarned');
    expect(inventory).not.toContain('markFollowWarned');
  });

  it('lives with the panel, not inside a feature', () => {
    // A panel preference owned by a feature is what made the shell
    // import from features/live-map/ to read it.
    expect(googleMaps).not.toContain('export async function getFollowPref');
    expect(settings).toContain("from '../prefs'");
  });
});
