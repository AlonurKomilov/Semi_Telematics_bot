/**
 * Two rules about the map's controls that a screenshot caught and a
 * test should have.
 *
 * 1. THE ENGINE IS THE ACCOUNT'S, NOT THE DEVICE'S.  For one version
 *    the panel stored "whose map" per browser.  The tile session
 *    refuses any account that is not already on Google, so the button
 *    sat permanently reading "Google · unavailable" — an offer that
 *    could never be taken — while the dashboard beside it changed the
 *    same setting account-wide and worked.
 *
 * 2. THE CONTROLS LIVE IN THE DASHBOARD'S CORNERS.  Zoom top-left,
 *    Map Layers top-right, Map Type bottom-left.  The corner a control
 *    lives in is muscle memory; somebody working in both screens should
 *    not have to look for the same two buttons in two places.
 */
import { describe, expect, it } from 'vitest';

import panelSrc from './LiveMapPanel.tsx?raw';
import controlsSrc from './MapControls.tsx?raw';
import prefsSrc from '../../prefs.ts?raw';

const panel = panelSrc as unknown as string;
const controls = controlsSrc as unknown as string;
const prefs = prefsSrc as unknown as string;

/** Comments explain the rule; they are not the rule.  A guard that
 *  reads prose matches its own explanation — that has happened here
 *  before, on `can_manage_inventory`. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
}

describe('which map the account is drawn on is not a device preference', () => {
  it('no key for it survives in prefs', () => {
    expect(code(prefs)).not.toContain('mapProvider');
  });

  it('the panel never writes the engine to storage', () => {
    const src = code(panel);
    expect(src).not.toContain('MAP_PROVIDER_KEY');
    // What it does instead: read the account's, and write the account's.
    expect(src).toContain('readEngine()');
    expect(src).toContain('setAccountEngine(');
  });

  it('the engine row is hidden from anyone the server would refuse', () => {
    // `config.all` is the ability the API reports for
    // can_manage_config_all — the same flag /map/config is gated on.
    expect(code(panel)).toContain("abilities.includes('config.all')");
    expect(code(controls)).toContain('canManageEngine');
  });

  it('a disabled engine row says WHY it is disabled', () => {
    // A dead control with no reason reads as broken software.
    expect(controls).toContain('account-wide setting');
  });
});

describe('the map’s controls name their stacking level once', () => {
  it('no control hardcodes a level beside its position', () => {
    // One name, one number: a second literal is how the first one
    // drifts.  That the number is BELOW `.menu`'s is checked where both
    // files can be read — tests/test_extension_stacking.py.
    expect(code(controls)).not.toMatch(/zIndex: \d/);
    expect(code(controls)).toMatch(/const CTL_Z = \d+/);
  });
});

describe('the controls are in the dashboard’s corners', () => {
  it('zoom is top-left', () => {
    expect(code(panel)).toContain("L.control.zoom({ position: 'topleft' })");
  });

  it('Map Layers is top-right and Map Type is bottom-left', () => {
    const src = code(controls);
    expect(src).toMatch(/top: 8, right: 8/);
    expect(src).toMatch(/bottom: 8, left: 8/);
  });
});

describe('an empty layer says why it is empty', () => {
  it('still separates "none here" from "your own filter is hiding them"', () => {
    expect(controlsSrc).toContain('None in this view');
    expect(controlsSrc).toContain('None of the chosen brands in this view');
  });

  it('names the source’s age beside them when the extract is old', () => {
    // The third reason, and the only one the panel could never have
    // guessed at: the data itself is behind.
    expect(controlsSrc).toContain('staleSourceAge');
    expect(controlsSrc).toMatch(/OSM data \$\{staleAge\} old/);
  });
});
