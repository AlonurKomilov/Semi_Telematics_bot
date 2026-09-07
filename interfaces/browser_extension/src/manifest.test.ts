/**
 * The manifest has to LOAD, and two of its keys look alike while
 * obeying different rules.
 *
 * `content_scripts[].matches` and `host_permissions` take a path, and
 * ours narrow to `/maps/*` on purpose — the overlay has no business on
 * the rest of google.com.  `web_accessible_resources[].matches` looks
 * identical and does NOT: Chrome requires the path there to be exactly
 * `/*`, and refuses to load the whole extension when it is not,
 * reporting only "Invalid match pattern" against an array index.
 *
 * That cost a sideload that would not install, on a manifest whose
 * three lists all said `https://www.google.com/maps/*` and looked
 * consistent for it.  A test knows the difference; reading does not.
 */
import { describe, expect, it } from 'vitest';
// Imported, not read from disk: the tests run in jsdom, where
// `import.meta.url` is an http URL and node's file helpers refuse it.
import raw from '../public/manifest.json';

const manifest = raw as {
  version: string;
  web_accessible_resources?: { resources: string[]; matches: string[] }[];
  content_scripts?: { matches: string[]; js: string[] }[];
  host_permissions?: string[];
};

describe('manifest', () => {
  it('gives every web-accessible resource an origin-only match', () => {
    for (const entry of manifest.web_accessible_resources ?? []) {
      for (const pattern of entry.matches) {
        expect(pattern, `${pattern} — Chrome requires the path to be exactly /*`)
          .toMatch(/^https:\/\/[^/]+\/\*$/);
      }
    }
  });

  it('still narrows the content script to the maps pages', () => {
    // The rule above must never be "fixed" by widening this one: the
    // overlay runs where the map is, and nowhere else on google.com.
    const scripts = manifest.content_scripts ?? [];
    expect(scripts).toHaveLength(1);
    expect(scripts[0].matches).toEqual(['https://www.google.com/maps/*']);
  });

  it('asks for the API and the maps pages, and nothing wider', () => {
    expect(manifest.host_permissions).toEqual([
      'https://api.4truck.us/*',
      'https://www.google.com/maps/*',
    ]);
  });

  it('carries a version the store will accept as new', () => {
    expect(manifest.version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
