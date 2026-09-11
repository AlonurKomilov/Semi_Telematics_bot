/**
 * The features this panel can show.  Live Map is #1; alerts, vehicle
 * lookup and the rest register here as folders under src/features/ —
 * the same one-home-per-feature rule the dashboard follows.
 */
import { lazy, type LazyExoticComponent, type ComponentType } from 'react';

/** What every panel feature is handed.  Abilities rather than flags:
 *  the server answers in the panel's vocabulary (/extension/me), so a
 *  feature hides a control the server would refuse instead of offering
 *  it and answering 403 on the press. */
export interface PanelFeatureProps {
  abilities: string[];
  /** The feature ids this person may open.  A feature sometimes needs
   *  to know what ANOTHER one is allowed to do — Inventory offers to
   *  point Google's map at a vehicle only when the person may see
   *  positions at all, which is exactly `live-map`. */
  features: string[];
}

export interface PanelFeature {
  id: string;
  label: string;
  Component: LazyExoticComponent<ComponentType<PanelFeatureProps>>;
  /** The feature's OWN configuration surface, when it has one.
   *
   *  Its presence is the whole mechanism: the shell puts a gear beside
   *  the feature's name only for a feature that declares one, so Live Map
   *  shows none and Inventory does, and a feature that grows config later
   *  needs no change to the shell at all.
   *
   *  Config, not Settings.  Settings (the user menu) holds what THIS
   *  PANEL does — follow a Google Maps tab, draw on the map.  This holds
   *  what the FEATURE means for the account: the two were kept apart on
   *  the dashboard for the same reason and must not merge here. */
  Config?: LazyExoticComponent<ComponentType<PanelFeatureProps>>;
}

export const FEATURES: PanelFeature[] = [
  { id: 'live-map', label: 'Live Map', Component: lazy(() => import('../features/live-map/LiveMapPanel')) },
  {
    id: 'inventory',
    label: 'Inventory',
    Component: lazy(() => import('../features/inventory/InventoryPanel')),
    Config: lazy(() => import('../features/inventory/InventoryConfig')),
  },
];

/** Which of these a person may open is the SERVER's answer — /extension/me
 *  returns feature ids, so the panel never learns what a permission flag
 *  is called and the grant-to-feature mapping is testable in one place. */
