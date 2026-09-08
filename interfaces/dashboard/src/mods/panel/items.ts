/**
 * Which component is a taxonomy item's own page.
 *
 * The /mods page's third level used to render the whole CATEGORY under
 * the item's name: `/mods/sounds/keyboard` and `/mods/sounds/interface`
 * were the same view with a different heading. The URL promised a depth
 * the page did not have, and the guard that should have noticed only
 * asked whether the item's NAME appeared somewhere below — which it
 * did, among its siblings.
 *
 * This map is the promise kept. One item, one component, and
 * `items.test.ts` holds it total over every browsable item in both
 * directions: an item with no page is a tile that opens on nothing, a
 * page with no item is one nobody can reach.
 *
 * Keyed as `category/item` rather than by item id alone, because ids
 * repeat across categories on purpose — `interface` is both a category
 * and Sound's first item.
 *
 * Size is deliberately absent. It is `panel: false` in the taxonomy
 * because it is one card of its own, and its two items are two halves
 * of that card — the page renders `SizeCard` whole for either.
 */
import { createElement, type ComponentType } from 'react';
import {
  AccentGroup, ModeGroup, CornersGroup, MaterialGroup, TypefaceGroup, IconsGroup,
  WallpaperGroup, CursorGroup, type LabelClass,
} from './Interface';
import { ShadersItem, MotionItem, AmbientItem } from './Effects';
import { SoundVolume, InterfaceSoundItem, KeyboardItem, LiveAlertsItem } from './Sounds';

export type ItemComponent = ComponentType<{ label: LabelClass }>;

/**
 * A component that takes no label — a switch with its own line — worn
 * as one that does, so the map has one shape.
 *
 * A real wrapper element rather than `C({})`: calling a function
 * component directly runs its hooks inside the CALLER, so a switch's
 * `usePreference` would attach to whatever rendered it and re-render
 * the wrong thing.
 */
const headless = (C: ComponentType): ItemComponent => {
  const Headless = () => createElement(C);
  Headless.displayName = `Headless(${C.displayName ?? C.name})`;
  return Headless;
};

export const ITEM_GROUPS: Record<string, ItemComponent> = {
  'interface/mode': ModeGroup,
  'interface/theme': AccentGroup,
  'interface/corners': CornersGroup,
  'interface/material': MaterialGroup,
  'interface/typeface': TypefaceGroup,
  'interface/icons': IconsGroup,
  'interface/wallpaper': WallpaperGroup,
  'interface/cursor': CursorGroup,
  'effects/shader': ShadersItem,
  'effects/motion': MotionItem,
  'effects/ambient': headless(AmbientItem),
  'sounds/interface': headless(InterfaceSoundItem),
  'sounds/keyboard': headless(KeyboardItem),
  'sounds/alerts': headless(LiveAlertsItem),
};

/**
 * A category's OWN control, shown on its page above the tiles.
 *
 * Sounds has one — the level and the cue set, shared by every lane that
 * makes a noise — and it is the reason the item pages are not the whole
 * category any more: what was shared moved up a level rather than being
 * repeated on every item under it.
 */
export const CATEGORY_CONTROLS: Record<string, ItemComponent> = {
  sounds: SoundVolume,
};
