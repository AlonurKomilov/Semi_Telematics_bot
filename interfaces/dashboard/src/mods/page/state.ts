/**
 * What a tile says about itself — pure readers over the taxonomy.
 *
 * The /mods page is the taxonomy drawn as depth: a hub of categories,
 * a category as a grid of items, an item as its control. The controls
 * already exist (`ModControls`, `SizeCard`); what the page adds is
 * STATE at a glance — which tiles a person has touched, and how much of
 * each category is dialled. Those are questions about stored values
 * against defaults, so they live here as functions of data, not inside
 * components, and are tested without rendering anything.
 */
import { MOD_DEFAULT, DEFS, type ModSetting, type SizeSetting } from '../../preferences';
import { motionPercent, THEME_PACKS, FONT_PACKS } from '../catalogue';
import { SOUND_PACKS } from '../sound/engine';
import { KEY_PACKS } from '../sound/keys';
import type { TaxonomyCategory, TaxonomyItem } from '../taxonomy';

/** How preference keys outside the theme blob are read. */
export type PrefReader = (key: string) => unknown;

export type TileState = 'default' | 'changed' | 'off';

const defaultOf = (key: string): unknown =>
  (DEFS as Record<string, { default: unknown }>)[key]?.default;

/**
 * An item is `changed` when any axis or key it owns differs from its
 * default; `off` when its only boolean gate is false; `default`
 * otherwise. `keep` axes count too — a person who chose Light has
 * changed the Color item even though a reset would not touch mode.
 */
export function itemState(
  item: TaxonomyItem,
  theme: ModSetting,
  prefs: PrefReader,
): TileState {
  const t = theme as unknown as Record<string, unknown>;
  const d = MOD_DEFAULT as unknown as Record<string, unknown>;
  const axisChanged = item.axes.some((a) => t[a] !== d[a]);
  const keys = item.prefs ?? [];
  const gates = keys.filter((k) => typeof defaultOf(k) === 'boolean');
  const prefChanged = keys.some((k) => prefs(k) !== defaultOf(k));
  if (!axisChanged && !prefChanged) {
    // A gated item at its default is OFF, not merely untouched — the
    // tile should say the thing is not running.
    return gates.length && gates.every((k) => prefs(k) === false) ? 'off' : 'default';
  }
  if (gates.length && gates.every((k) => prefs(k) === false)) return 'off';
  return 'changed';
}

/** How many of a category's items differ from default, and how many there are. */
export function categoryTouched(
  cat: TaxonomyCategory,
  theme: ModSetting,
  prefs: PrefReader,
): { changed: number; total: number } {
  const states = cat.items.map((i) => itemState(i, theme, prefs));
  return { changed: states.filter((s) => s === 'changed').length, total: states.length };
}

/**
 * The percentage a category's hub tile shows, or null when the
 * category has no single intensity.
 *
 * GX dials every category; ours honestly cannot. Sounds has a volume,
 * Effects has a motion scale (inverted — see `motionPercent`), Size has
 * a multiplier. Interface has no one number that means anything — a
 * colour is not 77% of a colour — so its tile shows how many items are
 * touched instead, via `categoryTouched`.
 */
export function categoryIntensity(
  cat: TaxonomyCategory,
  theme: ModSetting,
  size: SizeSetting,
  prefs: PrefReader,
): number | null {
  switch (cat.id) {
    case 'sounds': return Math.round(Number(prefs('mods.sound.volume')) * 100);
    case 'effects': return motionPercent(theme.motion);
    case 'size': return Math.round(size.global * 100);
    default: return null;
  }
}


// ── What the tile says the value IS ──────────────────────────────────

/**
 * Every id→label list the product already ships, in one lookup.
 *
 * The alternative was a per-item formatter, which is a seventh place
 * the catalogue's contents get restated. A pack that gains a member
 * gains its label here for free.
 */
const LABELS: ReadonlyArray<ReadonlyArray<{ id: string; label: string }>> = [
  THEME_PACKS, FONT_PACKS, SOUND_PACKS, KEY_PACKS,
];

/** Title-case a bare enum value — `pill` → `Pill`. The axes that are
 *  plain string lists (radius, material, icons) have no label anywhere,
 *  and inventing a fifth list for four words would be worse. */
const titleCase = (v: string) => v.charAt(0).toUpperCase() + v.slice(1);

/**
 * The lists are a PARAMETER so the lookup can be tested.
 *
 * Every id we ship today title-cases to exactly its own label — `mono`
 * to Mono, `azure` to Azure — so with the real lists the branch below is
 * indistinguishable from deleting it, and a mutation that ignored the
 * labels passed the whole suite. Measured. The branch earns its place
 * only for a pack whose label is not its id title-cased (`hi-contrast`
 * → "High contrast"), and that pack does not exist yet, so the test
 * supplies one.
 */
export function labelOf(
  value: unknown,
  lists: ReadonlyArray<ReadonlyArray<{ id: string; label: string }>> = LABELS,
): string | null {
  if (typeof value === 'boolean') return value ? 'On' : 'Off';
  if (typeof value === 'number') return `${Math.round(value * 100)}%`;
  if (typeof value !== 'string' || !value) return null;
  for (const list of lists) {
    const hit = list.find((e) => e.id === value);
    if (hit) return hit.label;
  }
  return titleCase(value);
}

/**
 * What the tile shows under its title — the current value, not just
 * whether it moved.
 *
 * "Changed" tells a person something happened and makes them click to
 * find out what; "Pill" tells them what they are looking at. GX shows a
 * thumbnail for the same reason: the tile answers the question instead
 * of announcing that it has an answer.
 *
 * Values are joined with a middot in the item's own axis order, and a
 * gate that is down short-circuits to "Off" — a keyboard pack means
 * nothing while typing makes no sound.
 */
export function itemSummary(
  item: TaxonomyItem,
  theme: ModSetting,
  prefs: PrefReader,
): string | null {
  const t = theme as unknown as Record<string, unknown>;
  const keys = item.prefs ?? [];
  const gates = keys.filter((k) => typeof defaultOf(k) === 'boolean');
  if (gates.length && gates.every((k) => prefs(k) === false)) return 'Off';

  const parts = [
    ...item.axes.map((a) => labelOf(t[a])),
    // A gate that is UP says so only when it has nothing else to show;
    // "On · Click" reads worse than "Click" for a pack that is playing.
    ...keys.filter((k) => !gates.includes(k)).map((k) => labelOf(prefs(k))),
  ].filter((v): v is string => v !== null);

  if (!parts.length && gates.length) return 'On';
  return parts.length ? parts.join(' · ') : null;
}
