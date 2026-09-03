/**
 * The features this panel can show.  Live Map is #1; alerts, vehicle
 * lookup and the rest register here as folders under src/features/ —
 * the same one-home-per-feature rule the dashboard follows.
 */
import { lazy, type LazyExoticComponent, type ComponentType } from 'react';

export interface PanelFeature {
  id: string;
  label: string;
  Component: LazyExoticComponent<ComponentType>;
}

export const FEATURES: PanelFeature[] = [
  { id: 'live-map', label: 'Live Map', Component: lazy(() => import('../features/live-map/LiveMapPanel')) },
];
