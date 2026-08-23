/**
 * Parking — right-click (context-menu) actions for a grid row.
 *
 * Feature-local builder for DataGrid's ``rowActions``, matching the house
 * pattern (features/<x>/contextMenu.tsx).
 *
 * Resolve appears ONLY on an unresolved row.  That per-row conditioning
 * is what let the Active and History tabs collapse into one grid: the
 * objection to merging them was "the actions differ", and ``rowActions``
 * is already a per-row function, so they can differ without splitting the
 * list in two.
 */
import { CheckCircle2, MapPin, History } from 'lucide-react';

import type { MenuAction } from '../../components/ui/context-menu';
import type { ParkingEvent } from './api';
import { mapsUrl } from './columns';

export function parkingRowMenu(
  ev: ParkingEvent,
  deps: {
    canResolve: boolean;
    openDetail: (e: ParkingEvent) => void;
    confirmResolve: (e: ParkingEvent) => void;
  },
): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'detail',
      label: 'Open details',
      icon: <History className="text-muted-foreground size-3.5" />,
      onSelect: () => deps.openDetail(ev),
    },
    {
      key: 'maps',
      label: 'Open in Google Maps',
      icon: <MapPin className="text-muted-foreground size-3.5" />,
      onSelect: () => window.open(
        mapsUrl(ev.latitude, ev.longitude), '_blank', 'noopener,noreferrer',
      ),
    },
  ];
  // Resolving a resolved row is not a no-op, it is a 400 from the API —
  // so the action is absent rather than disabled-and-failing.
  if (deps.canResolve && !ev.resolved) {
    actions.push({
      key: 'resolve',
      label: 'Resolve event',
      icon: <CheckCircle2 className="text-muted-foreground size-3.5" />,
      onSelect: () => deps.confirmResolve(ev),
    });
  }
  return actions;
}
