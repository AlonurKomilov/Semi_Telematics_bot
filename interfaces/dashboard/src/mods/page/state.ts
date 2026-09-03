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
import { motionPercent } from '../catalogue';
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
