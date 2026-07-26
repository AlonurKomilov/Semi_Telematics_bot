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
import { ArrowUpRight, ExternalLink, Pencil } from 'lucide-react';
import type { MenuAction } from '../../components/ui/context-menu';
import type { Vehicle } from '../../types';

export function vehicleRowMenu(
  vehicle: Vehicle,
  deps: {
    path: string;
    navigate: (path: string) => void;
    canManage: boolean;
    openEdit: (v: Vehicle) => void;
  },
): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'open',
      label: 'Open',
      icon: <ArrowUpRight size={14} className="text-muted-foreground" />,
      onSelect: () => deps.navigate(deps.path),
    },
    {
      key: 'open-new',
      label: 'Open in new tab',
      icon: <ExternalLink size={14} className="text-muted-foreground" />,
      onSelect: () => window.open(deps.path, '_blank', 'noopener'),
    },
  ];
  if (deps.canManage && vehicle.registry_id != null) {
    actions.push({
      key: 'edit',
      label: 'Edit vehicle',
      icon: <Pencil size={14} className="text-muted-foreground" />,
      separatorBefore: true,
      onSelect: () => deps.openEdit(vehicle),
    });
  }
  return actions;
}
