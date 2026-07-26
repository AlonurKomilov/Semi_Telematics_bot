/**
 * Alerts selection + bulk-action context.
 *
 * Things that don't belong in the URL but ARE shared across sections:
 *   • ``selected`` — Set of alert ids the user has checked.  URL-
 *     encoding a Set is awful, mutates very frequently (every
 *     checkbox), and has no meaning outside the current session.
 *   • ``expandedVehicles`` — legacy of the removed per-vehicle view;
 *     DataGrid owns group expansion now.  Inert, kept only so the
 *     context's shape stays stable for its tests.
 *   • ``bulkError`` / ``acking`` — transient UI state for the
 *     "Acknowledge N" flow at the page header.
 *
 * Sections that need these call ``useAlertsSelection()``.  Outside
 * the provider tree, the hook returns a no-op default rather than
 * throwing — easier to compose in tests.
 */
import {
  createContext,
  useContext,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react';
import type { Alert } from '../../../types';

export interface AlertsSelectionAPI {
  selected: Set<string | number>;
  setSelected: Dispatch<SetStateAction<Set<string | number>>>;
  /** Convenience: clears selection without needing the setter. */
  clearSelection: () => void;
  expandedVehicles: Set<string>;
  setExpandedVehicles: Dispatch<SetStateAction<Set<string>>>;
  bulkError: string;
  setBulkError: (s: string) => void;
  acking: boolean;
  setAcking: (a: boolean) => void;

  /** The alert currently shown in IncidentDrillInDrawer, or null when
   *  the drawer is closed.  AlertsResults sets this on row-id click;
   *  the drawer reads it.  On personas whose layout doesn't include
   *  the drawer, the click is recorded in state but nothing renders
   *  it — harmless. */
  drillInAlert: Alert | null;
  openDrillIn: (a: Alert) => void;
  closeDrillIn: () => void;
}

const NOOP_API: AlertsSelectionAPI = {
  selected: new Set(),
  setSelected: () => {},
  clearSelection: () => {},
  expandedVehicles: new Set(),
  setExpandedVehicles: () => {},
  bulkError: '',
  setBulkError: () => {},
  acking: false,
  setAcking: () => {},
  drillInAlert: null,
  openDrillIn: () => {},
  closeDrillIn: () => {},
};

const AlertsSelectionContext = createContext<AlertsSelectionAPI>(NOOP_API);

export function AlertsSelectionProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<Set<string | number>>(() => new Set());
  const [expandedVehicles, setExpandedVehicles] = useState<Set<string>>(
    () => new Set(),
  );
  const [bulkError, setBulkError] = useState('');
  const [acking, setAcking] = useState(false);
  const [drillInAlert, setDrillInAlert] = useState<Alert | null>(null);

  const api: AlertsSelectionAPI = useMemo(
    () => ({
      selected,
      setSelected,
      clearSelection: () => setSelected(new Set()),
      expandedVehicles,
      setExpandedVehicles,
      bulkError,
      setBulkError,
      acking,
      setAcking,
      drillInAlert,
      openDrillIn: (a: Alert) => setDrillInAlert(a),
      closeDrillIn: () => setDrillInAlert(null),
    }),
    [selected, expandedVehicles, bulkError, acking, drillInAlert],
  );

  return (
    <AlertsSelectionContext.Provider value={api}>
      {children}
    </AlertsSelectionContext.Provider>
  );
}

export function useAlertsSelection(): AlertsSelectionAPI {
  return useContext(AlertsSelectionContext);
}
