/**
 * Uniform props every Live Map overlay section accepts.
 *
 * Extended from the original ``MapOverlayProps`` (in
 * pages/live-map/overlays/types.ts) with the ``heatOn`` flag — the
 * host page owns the safety-heatmap toggle state, but every overlay
 * receives it via the shared sectionProps.  Overlays that don't care
 * simply ignore the field.  This keeps the section interface uniform
 * so PageLayoutHost can fan one prop object out to every section.
 */
import type { MutableRefObject } from 'react';
import type L from 'leaflet';
import type {
  MapVehicleFeature,
  MapVehicleProperties,
} from '../../../../types';

export interface LiveMapSectionProps {
  /** Imperative Leaflet map handle from useLeafletMap. */
  leafletMap: MutableRefObject<L.Map | null>;
  /** True once the Leaflet container has been initialised. */
  isReady: boolean;
  /** Current vehicle features rendered on the map. */
  vehicles: MapVehicleFeature[];
  /** Currently selected vehicle, or null for the all-vehicles view. */
  selected: MapVehicleProperties | null;
  /**
   * Side-panel safety-event heatmap toggle.  Owned by the host page
   * (defaults to ON for the Safety persona, OFF otherwise); the
   * SafetyEventOverlay reads it from here.  Other sections ignore.
   */
  heatOn: boolean;
  /**
   * Utilisation-heatmap toggle (the blue 30-day density layer on
   * Owner/Admin maps).  Persisted per user; defaults ON.  Only
   * UtilisationHeatmap reads it.
   */
  utilHeatOn: boolean;
  /**
   * Company-colour-dot toggle (per-company dots on Owner/Admin/
   * Accounting maps).  Persisted per user; defaults ON.  Only
   * CompanyColorPartition reads it.
   */
  companyColorsOn: boolean;
}
