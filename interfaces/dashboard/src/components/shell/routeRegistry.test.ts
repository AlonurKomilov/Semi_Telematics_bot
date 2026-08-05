/**
 * Drift guard: the command palette must be able to reach every route.
 *
 * `ROUTE_ENTRIES` is a hand-kept SIBLING of `config/featureCatalog.ts`.
 * It exists for a real reason — search needs three things the catalog
 * doesn't carry: an English label to match on (the catalog holds an i18n
 * KEY), a one-line description, and the words people actually type. But
 * two hand-kept lists drift, and this pair had: a responsive audit found
 * "loads", "mainten" and "applic" returning **No results** for pages that
 * exist. Thirteen live routes were unreachable from ⌘K.
 *
 * The auditor read it as prefix-matching; the scorer was fine. The
 * entries simply weren't there — which is exactly the failure a test
 * catches and a code review does not.
 */
import { describe, it, expect } from 'vitest';
import { FEATURE_CATALOG } from '../../config/featureCatalog';
import { ROUTE_ENTRIES } from './routeRegistry';

/** Routes deliberately kept OUT of search, each with its reason. Adding
 *  a path here is a decision; forgetting one is what the test prevents. */
const UNSEARCHABLE: Record<string, string> = {
  '/truck-anatomy': 'hidden learning model, not a product surface',
};

describe('command palette route coverage', () => {
  it('can reach every feature that has a route', () => {
    const searchable = new Set(ROUTE_ENTRIES.map((r) => r.path));
    const unreachable = FEATURE_CATALOG
      .filter((f) => f.path && !(f.path in UNSEARCHABLE))
      .map((f) => f.path)
      .filter((p) => !searchable.has(p));

    // Named, not counted — the failure message should say WHICH feature
    // someone added without making it findable.
    expect(unreachable).toEqual([]);
  });

  it('does not offer routes the catalog no longer has', () => {
    const real = new Set(FEATURE_CATALOG.map((f) => f.path));
    // Sub-routes the palette lists that the catalog does not model as
    // features are fine; what must not happen is offering a path that
    // was REMOVED. Anything here should resolve to a live route.
    const orphans = ROUTE_ENTRIES
      .map((r) => r.path)
      // A SUB-route of a catalog feature is legitimate — /reports has
      // four tabs the palette deep-links to, and the catalog models the
      // feature, not its tabs.
      .filter((p) => !real.has(p) && ![...real].some((f) => f !== '/' && p.startsWith(`${f}/`)))
      .filter((p) => !p.includes('/settings/') && p !== '/help');
    expect(orphans).toEqual([]);
  });

  it('gives every entry something to match on beyond its label', () => {
    // A one-word label with no keywords is only findable by typing that
    // exact word — which is how "work orders" stays reachable but "wo",
    // "repair" and "invoice" do not.
    const bare = ROUTE_ENTRIES
      .filter((r) => !r.description && !r.keywords?.length)
      .map((r) => r.path);
    expect(bare).toEqual([]);
  });
});
