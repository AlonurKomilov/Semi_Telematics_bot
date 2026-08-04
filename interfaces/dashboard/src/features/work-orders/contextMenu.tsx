/**
 * Work Orders — right-click (context-menu) actions.
 *
 * Feature-local builder for the grid's ``rowActions``: WHAT you can do to
 * a work order lives here; the page wires it up.  Named ``contextMenu`` —
 * not ``actions`` — because "actions" already means other things in this
 * codebase (DataGrid ``bulkActions``, the column ⋮ menus, AI write
 * actions); this file is only the right-click menu data.
 *
 * Handlers/permissions come in via ``deps`` — the builder stays a pure
 * data function over component-owned closures (testable, no god object).
 */
import { ArrowUpRight, ExternalLink } from 'lucide-react';
import { History } from 'lucide-react';
import type { MenuAction } from '../../components/ui/context-menu';
import type { WorkOrder } from '../../types';

export function workOrderRowMenu(
  wo: WorkOrder,
  deps: {
    navigate: (path: string) => void;
    openHistory?: (wo: WorkOrder) => void;
  },
): MenuAction[] {
  const path = `/work-orders/${wo.id}`;
  return [
    {
      key: 'open',
      label: 'Open',
      icon: <ArrowUpRight size={14} className="text-muted-foreground" />,
      onSelect: () => deps.navigate(path),
    },
    {
      key: 'open-new',
      label: 'Open in new tab',
      icon: <ExternalLink size={14} className="text-muted-foreground" />,
      onSelect: () => window.open(path, '_blank', 'noopener'),
    },
    ...(deps.openHistory ? [{
      key: 'history',
      label: 'View activity history',
      icon: <History size={14} className="text-muted-foreground" />,
      onSelect: () => deps.openHistory!(wo),
    }] : []),
  ];
}
