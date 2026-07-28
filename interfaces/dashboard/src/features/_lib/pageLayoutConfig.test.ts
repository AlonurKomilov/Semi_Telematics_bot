/**
 * The page-layout resolver — where every configuration tier meets.
 *
 * What must hold, and why each rule exists:
 *   - required sections survive EVERY tier (the queue is the page; the
 *     drawer is what deep links open — a config that removes them
 *     strands whoever saved it);
 *   - a section shipped AFTER the user customised appears (absent from
 *     the stored order means "new", never "hidden");
 *   - a section renamed/removed in code is dropped silently (stale ids
 *     must not haunt the gear or crash the page);
 *   - the frozen key string never changes shape (renaming it orphans
 *     every stored arrangement).
 */
import { describe, expect, it } from 'vitest';
import {
  canMove, gearItems, moveSection, resolvePageLayout, sanitizeRoleLayout,
  toggleSection,
} from './pageLayoutConfig';
import { pageKey, defFor, asPageLayoutPref } from '../../preferences/registry';
import type { SectionRegistry } from './types';

// Minimal registry stub — only fields the resolver reads.
const REG = {
  header:  { label: 'Header', required: true },
  queue:   { label: 'Queue', required: true },
  cards:   { label: 'Cards' },
  strip:   { label: 'Strip' },
  drawer:  { label: 'Drawer', required: true },
} as unknown as SectionRegistry;

const BASE = ['header', 'cards', 'strip', 'queue', 'drawer'];

describe('resolvePageLayout', () => {
  it('no preference → the base layout, untouched', () => {
    expect(resolvePageLayout(BASE, REG, null)).toEqual(BASE);
  });

  it('hides a hidden optional section and honours the stored order', () => {
    const out = resolvePageLayout(BASE, REG, {
      order: ['header', 'strip', 'cards', 'queue', 'drawer'],
      hidden: ['cards'],
    });
    expect(out).toEqual(['header', 'strip', 'queue', 'drawer']);
  });

  it('REQUIRED sections render even when a stored pref hides them', () => {
    // A pref saved before a section became required must not disable it.
    const out = resolvePageLayout(BASE, REG, {
      order: BASE,
      hidden: ['queue', 'cards'],
    });
    expect(out).toContain('queue');
    expect(out).not.toContain('cards');
  });

  it('a section shipped after the user customised is APPENDED, not lost', () => {
    const out = resolvePageLayout(BASE, REG, {
      order: ['header', 'cards', 'queue', 'drawer'],   // saved before 'strip' existed
      hidden: [],
    });
    expect(out).toEqual(['header', 'cards', 'queue', 'drawer', 'strip']);
  });

  it('ids the registry no longer knows are dropped silently', () => {
    const out = resolvePageLayout(BASE, REG, {
      order: ['header', 'ghost_section', 'cards', 'strip', 'queue', 'drawer'],
      hidden: ['ghost_section'],
    });
    expect(out).toEqual(['header', 'cards', 'strip', 'queue', 'drawer']);
  });

  it('keeps a known section the base lacks (a future role default may add one)', () => {
    const out = resolvePageLayout(['header', 'queue', 'drawer'], REG, {
      order: ['header', 'cards', 'queue', 'drawer'],
      hidden: [],
    });
    expect(out).toEqual(['header', 'cards', 'queue', 'drawer']);
  });
});

describe('gear item edits', () => {
  it('toggling hides and un-hides, but never a required section', () => {
    const items = gearItems(BASE, REG, null);
    const afterHide = toggleSection(items, 'cards');
    expect(afterHide.hidden).toEqual(['cards']);
    const afterShow = toggleSection(gearItems(BASE, REG, afterHide), 'cards');
    expect(afterShow.hidden).toEqual([]);
    // Required id: the toggle simply doesn't record it as hidden.
    const noOp = toggleSection(items, 'queue');
    expect(noOp.hidden).toEqual([]);
  });

  it('move swaps optional neighbours and clamps at the edges', () => {
    const items = gearItems(BASE, REG, null);
    // The only legal swap here is between the two optional neighbours —
    // everything else is edge-clamped or anchor-blocked (tested below).
    expect(moveSection(items, 'strip', -1).order)
      .toEqual(['header', 'strip', 'cards', 'queue', 'drawer']);
    expect(moveSection(items, 'header', -1).order).toEqual(BASE);   // clamped
    expect(moveSection(items, 'drawer', 1).order).toEqual(BASE);    // clamped
  });

  it('gear lists hidden sections too — they need a row to come back from', () => {
    const items = gearItems(BASE, REG, { order: BASE, hidden: ['strip'] });
    const strip = items.find((i) => i.id === 'strip');
    expect(strip?.hidden).toBe(true);
  });
});

describe('malformed stored values', () => {
  // The synced store is an opaque bag with no server-side shape check —
  // an older client, a future shape, or a corrupted row can put ANYTHING
  // under this key.  The resolver runs synchronously in page render,
  // ABOVE every section error boundary: a throw here doesn't drop one
  // section, it unmounts the whole route.
  const GARBAGE: unknown[] = [
    { order: null, hidden: [] },
    { order: {}, hidden: [] },
    { order: ['a'], hidden: 'x' },
    { order: [1, 2], hidden: [] },        // numbers, not ids
    'not-an-object',
    42,
    [],
    { unrelated: true },
  ];

  it('the resolver degrades every garbage shape to "not customised"', () => {
    for (const bad of GARBAGE) {
      expect(resolvePageLayout(BASE, REG, bad as never)).toEqual(BASE);
      expect(gearItems(BASE, REG, bad as never).map((i) => i.id)).toEqual(BASE);
    }
  });

  it('the store-level sanitize rejects the same shapes (first line of defence)', () => {
    const sanitize = defFor('page.alerts.layout')?.sanitize;
    expect(sanitize).toBeTypeOf('function');
    for (const bad of GARBAGE) {
      expect(sanitize!(bad)).toBeNull();
    }
    // ...and passes a valid shape through intact.
    expect(sanitize!({ order: ['a'], hidden: ['b'] }))
      .toEqual({ order: ['a'], hidden: ['b'] });
    expect(asPageLayoutPref(null)).toBeNull();
  });
});


describe('required sections are position anchors', () => {
  it('a move that would cross a required section is a no-op', () => {
    // BASE = header(req), cards, strip, queue(req), drawer(req)
    const items = gearItems(BASE, REG, null);
    // 'cards' sits directly below the required header — up is blocked.
    expect(moveSection(items, 'cards', -1).order).toEqual(BASE);
    // 'strip' sits directly above the required queue — down is blocked.
    expect(moveSection(items, 'strip', 1).order).toEqual(BASE);
    // ...but the two optional neighbours still swap with each other.
    expect(moveSection(items, 'cards', 1).order)
      .toEqual(['header', 'strip', 'cards', 'queue', 'drawer']);
  });

  it('canMove mirrors the same rule, so no arrow offers a dead click', () => {
    const items = gearItems(BASE, REG, null);
    const idx = (id: string) => items.findIndex((i) => i.id === id);
    expect(canMove(items, idx('cards'), -1)).toBe(false);  // header above
    expect(canMove(items, idx('cards'), 1)).toBe(true);    // strip below
    expect(canMove(items, idx('strip'), 1)).toBe(false);   // queue below
    expect(canMove(items, idx('header'), 1)).toBe(false);  // required itself
  });
});


describe('sanitizeRoleLayout (tier two enters the resolver)', () => {
  it('a valid team default passes, unknown ids filtered', () => {
    expect(sanitizeRoleLayout(
      ['header', 'ghost', 'strip', 'cards', 'queue', 'drawer'], REG,
    )).toEqual(['header', 'strip', 'cards', 'queue', 'drawer']);
  });

  it('a default missing a REQUIRED section is rejected WHOLESALE', () => {
    // Tier two is stricter than tier three because its blast radius is a
    // whole team: half-applying a stale default that predates a section
    // becoming required would strand everyone on that role at once.
    expect(sanitizeRoleLayout(['header', 'cards', 'queue'], REG)).toBeNull();
  });

  it('garbage shapes are rejected, not half-read', () => {
    for (const bad of [null, 'x', 42, {}, ['ok', 7], []]) {
      expect(sanitizeRoleLayout(bad, REG)).toBeNull();
    }
  });

  it('OPTION A end-to-end: a personal add survives a manager removal', () => {
    // The manager's team default omits 'strip'; this user had surfaced
    // it personally.  A default is not a lock — the user's arrangement
    // wins until the (later) lock flag exists.
    const teamDefault = sanitizeRoleLayout(
      ['header', 'cards', 'queue', 'drawer'], REG)!;
    const out = resolvePageLayout(teamDefault, REG, {
      order: ['header', 'strip', 'cards', 'queue', 'drawer'],
      hidden: [],
    });
    expect(out).toContain('strip');
  });

  it('...and a user with NO personal pref gets exactly the team default', () => {
    const teamDefault = sanitizeRoleLayout(
      ['header', 'cards', 'queue', 'drawer'], REG)!;
    expect(resolvePageLayout(teamDefault, REG, null)).toEqual(teamDefault);
  });
});


describe('the frozen key', () => {
  it('pageKey output shape is pinned — renaming orphans stored layouts', () => {
    expect(pageKey('alerts', 'layout')).toBe('page.alerts.layout');
  });

  it('the store resolves the family key (sync round-trip depends on it)', () => {
    expect(defFor('page.alerts.layout')).not.toBeNull();
    expect(defFor('page.alerts.unknownpart')).toBeNull();
  });
});
