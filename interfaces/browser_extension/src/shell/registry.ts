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
}

export interface PanelFeature {
  id: string;
  label: string;
  Component: LazyExoticComponent<ComponentType<PanelFeatureProps>>;
}

export const FEATURES: PanelFeature[] = [
  { id: 'live-map', label: 'Live Map', Component: lazy(() => import('../features/live-map/LiveMapPanel')) },
  { id: 'inventory', label: 'Inventory', Component: lazy(() => import('../features/inventory/InventoryPanel')) },
];

/** Which of these a person may open is the SERVER's answer — /extension/me
 *  returns feature ids, so the panel never learns what a permission flag
 *  is called and the grant-to-feature mapping is testable in one place. */
