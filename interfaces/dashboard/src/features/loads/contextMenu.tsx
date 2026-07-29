/**
 * Loads — right-click (context-menu) actions.
 *
 * Feature-local builder for the grid's ``rowActions``.  Named
 * ``contextMenu`` — not ``actions`` — to stay distinct from DataGrid's
 * own action vocabulary (bulkActions, column ⋮ menus).
 *
 * The edit gate comes in via ``deps.canEditLoad`` — the SAME predicate the
 * row click uses.  Since 2026-07-29 that's simply "holds Manage": the
 * own-scope wall came down; the load's history trail answers "who
 * changed what" instead.
 */
import { Pencil } from 'lucide-react';
import type { MenuAction } from '../../components/ui/context-menu';
import type { LoadRow } from './api';

export function loadRowMenu(
  load: LoadRow,
  deps: {
    canEditLoad: (r: LoadRow) => boolean;
    openEdit: (r: LoadRow) => void;
  },
): MenuAction[] {
  if (!deps.canEditLoad(load)) return [];
  return [
    {
      key: 'edit',
      label: 'Edit load',
      icon: <Pencil size={14} className="text-muted-foreground" />,
      onSelect: () => deps.openEdit(load),
    },
  ];
}
