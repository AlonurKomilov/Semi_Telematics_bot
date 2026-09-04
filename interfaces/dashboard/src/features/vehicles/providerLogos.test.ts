/**
 * The generated marks must still BE the canonical assets.
 *
 * providerMarks.tsx is generated from
 * capabilities/integrations/<provider>/assets/logo.svg. A hand edit to
 * either side is the way a logo quietly becomes a slightly different
 * logo, so this re-derives from the asset and compares the geometry.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { PROVIDER_LOGO, providerLogo } from './providerLogos';

const ASSET: Record<string, string> = {
  samsara: 'capabilities/integrations/samsara/assets/logo.svg',
};
// From interfaces/dashboard/src/features/vehicles → repo root.
const REPO = resolve(__dirname, '../../../../..');
const ART = readFileSync(resolve(__dirname, 'providerMarks.tsx'), 'utf8');
/** Prose is not code: this file's own comment says the words "<img src>"
 *  to explain why it does not use one, and the first version of the
 *  guard below matched that sentence. */
const CODE = ART.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
const paths = (svg: string) => [...svg.matchAll(/\sd="([^"]+)"/g)].map((m) => m[1]);

describe('provider marks match the assets they were generated from', () => {
  it('every provider with a mark has its canonical asset listed here', () => {
    expect(Object.keys(PROVIDER_LOGO).sort()).toEqual(Object.keys(ASSET).sort());
  });

  for (const [provider, rel] of Object.entries(ASSET)) {
    it(`${provider}: same paths and same viewBox as ${rel}`, () => {
      const svg = readFileSync(resolve(REPO, rel), 'utf8');
      const assetPaths = paths(svg);
      expect(assetPaths.length).toBeGreaterThan(0);
      for (const d of assetPaths) expect(ART).toContain(d);
      const vb = /viewBox="([^"]+)"/.exec(svg)?.[1];
      expect(ART).toContain(`viewBox="${vb}"`);
    });
  }

  it('the mark inherits its colour — an <img> would render it black', () => {
    expect(CODE).toContain('fill="currentColor"');
    expect(CODE).not.toMatch(/<img[\s/>]/);
  });

  it('Samsara is a wordmark, so it stands in place of the name', () => {
    expect(providerLogo('samsara')?.kind).toBe('wordmark');
    expect(providerLogo('datatruck')).toBeNull();
  });
});
