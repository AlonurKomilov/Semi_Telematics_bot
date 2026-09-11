/**
 * The gear belongs to the FEATURE, and only to a feature that has one.
 *
 * The owner asked for this shape: config per feature, reached from a gear
 * beside the feature's name in the panel's own header, and absent for a
 * feature with nothing to configure — Live Map shows none. The mechanism
 * is the optional `Config` on a registry entry, so a feature that grows
 * config later needs no change to the shell at all.
 *
 * jsdom performs no layout and the shell is lazy all the way down, so
 * these are lexical, the same way the splitter's contract is.
 */
import { describe, expect, it } from 'vitest';

import appSrc from './App.tsx?raw';
import registrySrc from './registry.ts?raw';
import { FEATURES } from './registry';

const app = appSrc as unknown as string;
const registry = registrySrc as unknown as string;

describe('config is a property of a feature, not of the panel', () => {
  it('is optional, so a feature without one declares nothing', () => {
    expect(registry).toContain('Config?: LazyExoticComponent');
  });

  it('Live Map has none and Inventory has one', () => {
    const byId = Object.fromEntries(FEATURES.map((f) => [f.id, f]));
    expect(byId['live-map'].Config).toBeUndefined();
    expect(byId.inventory.Config).toBeDefined();
  });

  it('the gear renders only when the feature declares a surface', () => {
    // Not `abilities.includes(...)` and not a hardcoded feature id: an
    // always-present gear that opens nothing for half the panel is a
    // control somebody has to learn twice.
    expect(app).toContain('{feature.Config && (');
    expect(app).toContain("setView(view === 'config' ? 'feature' : 'config')");
  });

  it('switching feature leaves that feature’s config', () => {
    // The gear is the only way in, and Live Map has no gear — staying in
    // `config` would strand the panel on a view its new feature cannot
    // render.
    const pick = app.split('const pickFeature =')[1].split('};')[0];
    expect(pick).toContain("setView('feature')");
  });

  it('keeps Config and Settings apart', () => {
    // Settings is what this PANEL does for me; config is what the FEATURE
    // means for the account. The dashboard keeps them apart for the same
    // reason and the panel must not merge them.
    expect(app).toContain("type View = 'feature' | 'settings' | 'config';");
    expect(app).toContain("onSettings={() => setView('settings')}");
  });
});
