/**
 * Page-section layout resolution — the three configuration tiers, in one
 * pure function (owner decision, 2026-07-27; Option A):
 *
 *   1. PERMISSION  — not resolved here.  Sections self-gate their data,
 *      and the render-side permission filter applies regardless of any
 *      config, so no tier below can ever WIDEN what a user sees.
 *   2. ROLE DEFAULT — a role manager's team-wide arrangement (server
 *      table; arrives with the manager tier).  Replaces the shipped
 *      persona layout as the BASE when present.
 *   3. USER PREFERENCE — one person's own arrangement over that base.
 *      Affects nobody else; lives in the preferences service.
 *
 * Config only arranges — it never grants.  And ``required`` sections
 * (the queue itself; the drill-in drawer that deep links need) survive
 * every tier: a page you can configure into uselessness strands whoever
 * did it.
 */
import type { PageLayoutPref } from '../../preferences';
import { asPageLayoutPref } from '../../preferences/registry';
import type { SectionRegistry } from './types';

/**
 * Resolve the section list actually rendered.
 *
 * ``base`` is the persona's shipped layout — or the manager's role
 * default once that tier lands (the caller passes whichever applies, so
 * this function doesn't change when tier 2 arrives).
 *
 * Reconciliation rules, each protecting against a real drift:
 *   - ids not in the registry are DROPPED (a section renamed/removed in
 *     code after the user stored it — stale ids must not crash or haunt
 *     the gear);
 *   - base ids missing from the stored order are APPENDED (a section
 *     shipped after the user customised: absent-from-order means "new",
 *     never "hidden" — that's why the pref stores order and hidden
 *     separately);
 *   - ids in the stored order but NOT in the base are kept only if the
 *     registry knows them (a manager may later add a section the persona
 *     default lacks; the user's arrangement of it survives).  OPEN for
 *     increment 2: this same branch keeps a section a manager REMOVED
 *     from the team base if a user had personally surfaced it — decide
 *     then whether a manager removal outranks a personal add, because
 *     changing it later silently reinterprets stored prefs;
 *   - ``required`` sections render even when hidden lists them (a stored
 *     pref from before a section became required must not disable it).
 */
export function resolvePageLayout<P extends object>(
  base: string[],
  registry: SectionRegistry<P>,
  pref: PageLayoutPref | null,
): string[] {
  const known = (id: string) => Object.prototype.hasOwnProperty.call(registry, id);
  const isRequired = (id: string) => registry[id]?.required === true;

  // Runs synchronously in page render, ABOVE every section error
  // boundary — the store's sanitize is the first line of defence, this
  // guard is the second.  Malformed input degrades to "not customised";
  // a throw here unmounts the whole route, which is a worse failure
  // than the required-floor was designed to prevent.
  pref = asPageLayoutPref(pref);
  if (!pref) return base.filter(known);

  const ordered = pref.order.filter(known);
  const inOrder = new Set(ordered);
  const appended = base.filter((id) => known(id) && !inOrder.has(id));
  const full = [...ordered, ...appended];

  const hidden = new Set(pref.hidden);
  return full.filter((id) => isRequired(id) || !hidden.has(id));
}

/** The gear's item model — everything the popover needs per section. */
export interface GearItem {
  id: string;
  label: string;
  required: boolean;
  hidden: boolean;
}

/**
 * The full, ordered item list the gear shows — INCLUDING hidden
 * sections (they need a row to be re-enabled from) and required ones
 * (shown disabled-with-reason rather than omitted; a control that
 * vanishes teaches nothing).
 */
export function gearItems<P extends object>(
  base: string[],
  registry: SectionRegistry<P>,
  pref: PageLayoutPref | null,
): GearItem[] {
  const known = (id: string) => Object.prototype.hasOwnProperty.call(registry, id);
  const safe = asPageLayoutPref(pref);   // same second-line guard as above
  const ordered = safe
    ? [...safe.order.filter(known),
       ...base.filter((id) => known(id) && !safe.order.includes(id))]
    : base.filter(known);
  const hidden = new Set(safe?.hidden ?? []);
  return ordered.map((id) => ({
    id,
    label: registry[id]?.label ?? id,
    required: registry[id]?.required === true,
    hidden: !registry[id]?.required && hidden.has(id),
  }));
}

/** Toggle one section's visibility; returns the next pref to store. */
export function toggleSection(
  items: GearItem[],
  id: string,
): PageLayoutPref {
  return {
    order: items.map((i) => i.id),
    hidden: items
      .filter((i) => (i.id === id ? !i.hidden : i.hidden))
      .filter((i) => !i.required)
      .map((i) => i.id),
  };
}

/** Move one section up/down; returns the next pref to store.
 *
 *  Required sections are position ANCHORS, not just un-hideable: a move
 *  that would cross one is a no-op.  Without this, one click could put a
 *  hero card above the page header or below the queue — required
 *  sections hold the page's shape, and optional ones reorder among
 *  themselves between those anchors. */
export function moveSection(
  items: GearItem[],
  id: string,
  direction: -1 | 1,
): PageLayoutPref {
  const order = items.map((i) => i.id);
  const hidden = items.filter((i) => i.hidden).map((i) => i.id);
  const from = order.indexOf(id);
  const to = from + direction;
  if (from === -1 || to < 0 || to >= order.length) return { order, hidden };
  if (items[from].required || items[to].required) return { order, hidden };
  [order[from], order[to]] = [order[to], order[from]];
  return { order, hidden };
}

/** Can this item move that way?  The gear disables the arrow (with the
 *  same no-op rule as moveSection) instead of offering a dead click. */
export function canMove(items: GearItem[], idx: number, direction: -1 | 1): boolean {
  const to = idx + direction;
  if (to < 0 || to >= items.length) return false;
  return !items[idx].required && !items[to].required;
}
