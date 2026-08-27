/**
 * Vehicles — right-click (context-menu) actions.
 *
 * Feature-local builder for the grid's ``rowActions``.  Named
 * ``contextMenu`` — not ``actions`` — to stay distinct from DataGrid's
 * own action vocabulary (bulkActions, column ⋮ menus).
 *
 * ``deps.path`` is the detail-page URL the page computes (vehiclePath —
 * carries the ``?company=`` disambiguator); Edit appears only for
 * managers on registry-backed rows, mirroring the inline pencil column.
 */
import { ArrowUpRight, ExternalLink, Pencil, RotateCcw } from 'lucide-react';
import type { MenuAction } from '../../components/ui/context-menu';
import type { Vehicle } from '../../types';

export function vehicleRowMenu(
  vehicle: Vehicle,
  deps: {
    path: string;
    navigate: (path: string) => void;
    canManage: boolean;
    openEdit: (v: Vehicle) => void;
    /** Bring a retired truck back — omitted on live rows. */
    restore?: (v: Vehicle) => void;
  },
): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'open',
      label: 'Open',
      icon: <ArrowUpRight className="text-muted-foreground size-3.5" />,
      onSelect: () => deps.navigate(deps.path),
    },
    {
      key: 'open-new',
      label: 'Open in new tab',
      icon: <ExternalLink className="text-muted-foreground size-3.5" />,
      onSelect: () => window.open(deps.path, '_blank', 'noopener'),
    },
  ];
  if (deps.canManage && vehicle.registry_id != null) {
    actions.push({
      key: 'edit',
      label: 'Edit vehicle',
      icon: <Pencil className="text-muted-foreground size-3.5" />,
      separatorBefore: true,
      onSelect: () => deps.openEdit(vehicle),
    });
  }
  // Only on an archived row, and only for someone who can manage
  // vehicles.  One act: the telematics link was never cleared when the
  // truck was archived, so restoring re-links nothing — the ingest
  // simply stops skipping it and readings resume on the next tick.
  if (deps.restore && vehicle.archived && vehicle.registry_id != null) {
    actions.push({
      key: 'restore',
      label: 'Restore to fleet',
      icon: <RotateCcw className="text-muted-foreground size-3.5" />,
      separatorBefore: true,
      onSelect: () => deps.restore!(vehicle),
    });
  }
  return actions;
}
