/**
 * Inventory card — what physically lives in this truck (dashcam, fuel
 * card, toll transponder, ELD, tablet, other).
 *
 * Each row: category icon · label · identifier (mono — the theft-proof
 * part) · status chip (tone helper) · verification freshness (the
 * Freshness family — a 41-day-old check announces itself).  Row click
 * opens the item dialog (details, actions, accountability trail).
 *
 * VIEW rides normal vehicle access; the Add button and every action are
 * gated on ``can_manage_vehicles`` (registry admin).
 */
import { useState } from 'react';
import { Plus } from 'lucide-react';
import { CardSkeleton } from '../../../components/shell';
import { Button } from '../../../components/ui/button';
import { Freshness } from '../../../components/tooltip';
import { statusClasses, toneText } from '../../../lib/status';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import type { VehicleSectionProps } from '../sections/_shared/types';
import { useInventory } from './useInventory';
import type { InventoryItem } from './useInventory';
import { categoryMeta, STATUS_LABELS } from './categories';
import { AddItemDialog, ItemDialog } from './ItemDialog';

export default function InventoryCard({ vehicleName, company }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  const canManage = has('can_manage_vehicles');
  const { data, isLoading } = useInventory(vehicleName, company);
  const [addOpen, setAddOpen] = useState(false);
  const [selected, setSelected] = useState<InventoryItem | null>(null);

  if (isLoading) return <CardSkeleton height="h-48" />;
  if (!data) return null;

  const { items, summary } = data;

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-lg font-semibold">Inventory</h2>
          {summary.total > 0 && (
            <span className="text-2xs text-muted-foreground">
              {summary.total} item{summary.total === 1 ? '' : 's'}
              {summary.attention > 0 && (
                <> · <span className={toneText('warn')}>
                  {summary.attention} need{summary.attention === 1 ? 's' : ''} attention
                </span></>
              )}
            </span>
          )}
        </div>
        {canManage && (
          <Button variant="outline" size="xs" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add
          </Button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing tracked in this truck yet.
          {canManage ? ' Add the dashcam, fuel card, ELD… so swaps and losses stay accountable.' : ''}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item) => {
            const { label: catLabel, Icon } = categoryMeta(item.category);
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setSelected(item)}
                  className="w-full flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-muted/40 transition"
                >
                  <Icon size={16} className="text-muted-foreground shrink-0" />
                  <span className="flex-1 min-w-0">
                    <span className="text-sm text-foreground truncate block">
                      {item.label}
                      {item.identifier && (
                        <span className="font-mono text-xs text-muted-foreground ml-2">{item.identifier}</span>
                      )}
                    </span>
                    <span className="text-2xs text-muted-foreground">{catLabel}</span>
                  </span>
                  <span className={`px-2 py-0.5 rounded-md text-xs border shrink-0 ${statusClasses(item.status)}`}>
                    {STATUS_LABELS[item.status] ?? item.status}
                  </span>
                  {item.last_verified_at ? (
                    <Freshness ts={item.last_verified_at}>
                      <span className="text-2xs text-muted-foreground shrink-0">verified</span>
                    </Freshness>
                  ) : (
                    <span className="text-2xs text-muted-foreground/60 shrink-0">never verified</span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {canManage && addOpen && (
        <AddItemDialog
          vehicleName={vehicleName}
          company={company}
          categories={data.categories}
          onClose={() => setAddOpen(false)}
        />
      )}
      {selected && (
        <ItemDialog
          vehicleName={vehicleName}
          company={company}
          item={selected}
          statuses={data.statuses}
          canManage={canManage}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
