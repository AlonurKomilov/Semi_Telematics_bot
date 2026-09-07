/**
 * THE ONE DECLARATION of what Mods is made of.
 *
 * Four categories, and inside each the items a person sees — on the
 * /mods page as tiles, in the panel as headed groups, on the profile
 * card as sections. Before this file existed, that shape was written out
 * by hand in six places (MOD_SECTIONS, MOD_FIELD_SECTION, SECTION_ORDER,
 * SECTION_AXES and two test tables) and after only four axes they had
 * already stopped agreeing: `size` was in three of them, `mods` mapped
 * to no field, `entrance` had a reset and no control, `font` was routed
 * and never applied. Every one of those is now DERIVED from here, so a
 * category can gain an item — or an item can change category, which is
 * the most volatile fact in this service — in one line.
 *
 * A LEAF. Imports nothing at runtime, so anything may import it without
 * closing a ring: the catalogue, the panel, the card, the page, the
 * registry, the guards. The types it names come from the catalogue as
 * type-only imports, which are erased.
 *
 * What it deliberately does NOT hold: defaults (the registry owns the
 * stored shape), option lists (the catalogue owns what a pack is), and
 * controls (the panel owns how a thing is set). This is the map; those
 * are the territory.
 */
import type { Mod } from './catalogue';

/** A Mod's own fields — what a look may carry. Everything but the three
 *  metadata fields, which describe the mod rather than change the app. */
export type ModField = keyof Omit<Mod, 'id' | 'label' | 'why'>;

export interface TaxonomyItem {
  /** Stable id — tile key on the page, anchor in the card. */
  readonly id: string;
  /** What a person reads. English, untranslated, like pack names. */
  readonly title: string;
  /**
   * The caps heading the PANEL renders for this item, if it renders one.
   * Interface items each head their own chip row; Sound's items are
   * switches beneath one shared heading and have none of their own.
   */
  readonly heading?: string;
  /**
   * The `ModSetting` axes this item owns. Its reset restores these;
   * its tile on the page reads them for state.
   */
  readonly axes: readonly string[];
  /**
   * Axes that are SHOWN here but never reset — `mode` is the one. A
   * reset that threw a light-mode user into dark would be the same
   * mistake the reset exists to undo, arriving from the other side.
   */
  readonly keep?: readonly string[];
  /** Preference keys outside `mods.theme` this item owns. */
  readonly prefs?: readonly string[];
  /**
   * The Mod fields a look uses to carry this, if a look can.
   *
   * A LIST, because an item can be two halves of one question: Icons
   * owns both the pack and the weight, and a single `modField` meant a
   * look could carry one of them and not the other — which is how
   * `iconPack` was declarable nowhere while `icons` was.
   */
  readonly modFields?: readonly ModField[];
  /**
   * A look may switch this on; a person may not.
   *
   * It still belongs to its category — the reset clears it, a mod
   * carries it, the category tile counts it as changed — but it has no
   * control of its own, so it gets no tile and no page. Without the
   * flag, `entrance` shipped a tile that opened a page headed
   * "Entrance" showing Motion and Ambient: the page promises the thing
   * it is named after, and the promise was empty.
   *
   * The rule this expresses was already written down — the catalogue
   * shows only what a mod can SUPPLY — but it was written as a comment
   * and comments do not remove tiles.
   */
  readonly modOnly?: boolean;
}

export interface TaxonomyCategory {
  readonly id: 'interface' | 'sounds' | 'effects' | 'size';
  readonly title: string;
  /**
   * Whether `ModControls` renders this as a section. Size does not —
   * it is a whole card of its own (`SizeCard`), and pretending
   * otherwise is how `size` ended up in three of six lists and absent
   * from the fourth.
   */
  readonly panel: boolean;
  /** Category-level heading and keys — things shared by every item. */
  readonly heading?: string;
  readonly prefs?: readonly string[];
  readonly items: readonly TaxonomyItem[];
}

export const TAXONOMY: readonly TaxonomyCategory[] = [
  {
    id: 'interface',
    title: 'Interface',
    panel: true,
    items: [
      { id: 'theme',    title: 'Color',    heading: 'Color',
        axes: ['mode', 'accent', 'brand', 'canvas', 'surfaces'],
        keep: ['mode'], modFields: ['accent'] },
      { id: 'corners',  title: 'Corners',  heading: 'Corners',  axes: ['radius'],   modFields: ['radius'] },
      { id: 'material', title: 'Material', heading: 'Material', axes: ['material'], modFields: ['material'] },
      { id: 'typeface', title: 'Typeface', heading: 'Typeface', axes: ['font'],     modFields: ['font'] },
      // Two halves of one question — WHICH glyphs, and how heavily they
      // are drawn — so one item, one reset, one tile, and a look may
      // carry either or both.
      { id: 'icons',    title: 'Icons',    heading: 'Icons',
        axes: ['icons', 'iconPack'], modFields: ['icons', 'iconPack'] },
      // Its own item and not part of Color, because it is not a colour:
      // Color decides what everything is MADE of and every surface
      // derives from it, while a wallpaper is one pattern on one
      // surface. Folding it into Color would put a decision about the
      // chrome inside the question that repaints the whole app.
      { id: 'wallpaper', title: 'Wallpaper', heading: 'Wallpaper',
        axes: ['wallpaper'], modFields: ['wallpaper'] },
      { id: 'cursor', title: 'Cursor', heading: 'Cursor',
        axes: ['cursor'], modFields: ['cursor'] },
    ],
  },
  {
    id: 'sounds',
    title: 'Sounds',
    panel: true,
    heading: 'Sound',
    // One volume above every item — engine.test.ts holds the line that
    // Sounds has exactly one intensity.
    prefs: ['mods.sound.volume'],
    items: [
      { id: 'interface', title: 'Interface sounds',
        axes: [], prefs: ['mods.sound.ui', 'mods.sound.pack'], modFields: ['sound'] },
      { id: 'keyboard',  title: 'Keyboard',
        axes: [], prefs: ['mods.sound.keyboard', 'mods.sound.keyboard.pack'] },
      { id: 'alerts',    title: 'Live alerts',
        axes: [], prefs: ['dispatch.soundOn'] },
    ],
  },
  {
    id: 'effects',
    title: 'Effects',
    panel: true,
    items: [
      { id: 'motion',   title: 'Motion',   heading: 'Motion', axes: ['motion'],   modFields: ['motion'] },
      // A look may switch the page entrance on; the panel offers no
      // control, and the effects reset still clears it.
      { id: 'entrance', title: 'Entrance', axes: ['entrance'], modFields: ['entrance'],
        modOnly: true },
      { id: 'ambient',  title: 'Ambient mode', axes: [], prefs: ['mods.ambient'] },
    ],
  },
  {
    id: 'size',
    title: 'Size',
    panel: false,
    prefs: ['mods.size'],
    items: [
      { id: 'global',  title: 'Everything', axes: [], modFields: ['size'] },
      { id: 'regions', title: 'By area',    axes: [] },
    ],
  },
];

export type CategoryId = TaxonomyCategory['id'];

// ── Derivations. Each replaces a table that used to be typed by hand. ──

/** Category ids in render order. Was SECTION_ORDER. */
export const CATEGORY_IDS: readonly CategoryId[] = TAXONOMY.map((c) => c.id);

/** The categories `ModControls` renders, plus the container's own row.
 *  `mods` is not a category — it is how the card asks for the mod
 *  chips — and it lives here so the list has one home. Was MOD_SECTIONS. */
export const PANEL_SECTIONS = [
  'mods',
  ...TAXONOMY.filter((c) => c.panel).map((c) => c.id),
] as const;

/** What `ModControls` narrows itself to. A fact about the product, so it
 *  lives with the product's shape rather than with the component that
 *  happens to branch on it. */
export type ModSection = (typeof PANEL_SECTIONS)[number];

/** Which category a Mod field writes into. Was MOD_FIELD_SECTION, and
 *  typed as a total Record so a Mod field with no item is a build error
 *  rather than a footprint that silently omits it. */
export const MOD_FIELD_CATEGORY = Object.fromEntries(
  TAXONOMY.flatMap((c) => c.items.flatMap((i) => (i.modFields ?? []).map((f) => [f, c.id]))),
) as Record<ModField, CategoryId>;

/** The `ModSetting` axes a category's reset restores — every item's
 *  axes minus the ones marked `keep`. Was the key set of SECTION_AXES. */
export function resetAxesOf(id: CategoryId): readonly string[] {
  const cat = TAXONOMY.find((c) => c.id === id);
  if (!cat) return [];
  return cat.items.flatMap((i) => i.axes.filter((a) => !(i.keep ?? []).includes(a)));
}

/** The caps headings the panel renders for a category, in order. */
export function headingsOf(id: CategoryId | 'mods'): readonly string[] {
  if (id === 'mods') return ['Mods'];
  const cat = TAXONOMY.find((c) => c.id === id);
  if (!cat) return [];
  return [
    ...(cat.heading ? [cat.heading] : []),
    ...cat.items.flatMap((i) => (i.heading ? [i.heading] : [])),
  ];
}

export const categoryById = (id: string): TaxonomyCategory | undefined =>
  TAXONOMY.find((c) => c.id === id);

/**
 * The items a person can open — everything a look supplies but nobody
 * sets is left out.
 *
 * Only the PAGE uses this. Resets, mod fields and the category tile's
 * changed-count all still walk `items` in full: a mod-only axis is part
 * of its category in every way except having somewhere to be clicked.
 */
export function browsableItemsOf(id: CategoryId): readonly TaxonomyItem[] {
  return (categoryById(id)?.items ?? []).filter((i) => !i.modOnly);
}
