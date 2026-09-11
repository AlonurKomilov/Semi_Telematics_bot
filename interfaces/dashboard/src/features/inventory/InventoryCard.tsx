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
 * gated on ``can_manage_inventory`` — the feature's own
 * grant since it left features/vehicles/, so a person may read a
 * truck's kit without administering the registry.
 */
import { useState } from 'react';
import { Plus } from '../../lib/icons';
import { CardSkeleton } from '../../components/shell';
import { Button } from '../../components/ui/button';
import { Freshness } from '../../components/tooltip';
import { statusClasses, toneText } from '../../lib/status';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import type { VehicleSectionProps } from '../vehicles/sections/_shared/types';
import { useInventory } from './useInventory';
import type { InventoryItem } from './useInventory';
import { categoryMeta, STATUS_LABELS } from './categories';
import { AddItemDialog, ItemDialog } from './ItemDialog';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

export default function InventoryCard({ vehicleName, company }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  const canManage = has('can_manage_inventory');
  const { data, isLoading } = useInventory(vehicleName, company);
  const [addOpen, setAddOpen] = useState(false);
  const [selected, setSelected] = useState<InventoryItem | null>(null);

  if (isLoading) return <CardSkeleton height="h-48" />;
  if (!data) return null;

  const { items, summary, coverage } = data;
  // An account with no template has not said what "complete" means, so
  // nothing here says it either.  Silence is the honest answer; "0 of 0"
  // would read as a verdict nobody pronounced.
  const declared = coverage.expected > 0;
  const shortRows = coverage.rows.filter((r) => r.short > 0);

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <SectionHeader>Inventory</SectionHeader>
          {(summary.total > 0 || declared) && (
            <span className="text-2xs text-muted-foreground">
              {summary.total} item{summary.total === 1 ? '' : 's'}
              {summary.attention > 0 && (
                <> · <span className={toneText('warn')}>
                  {summary.attention} need{summary.attention === 1 ? 's' : ''} attention
                </span></>
              )}
              {/* A DIFFERENT fact from the count beside it: that one says
                  how much is RECORDED, this says how much of what the
                  truck owes is aboard.  A truck can carry six items and
                  still be short its ELD. */}
              {declared && (
                <> · <span className={coverage.present < coverage.expected ? toneText('warn') : undefined}>
                  {coverage.present} of {coverage.expected} expected aboard
                </span></>
              )}
            </span>
          )}
        </div>
        {canManage && (
          <Button variant="outline" size="xs" onClick={() => setAddOpen(true)}>
            <Plus /> Add
          </Button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {declared
            // The case this whole template exists for: a truck owing
            // three things and recording none looked identical to a
            // truck that owes nothing.  Now it says what it owes.
            ? `Nothing recorded yet — this truck is expected to carry ${coverage.expected} item${coverage.expected === 1 ? '' : 's'}.`
            : 'Nothing tracked in this truck yet.'}
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
                  <Icon className="text-muted-foreground shrink-0 size-4" />
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

      {/* What is SHORT, named.  A count tells you something is wrong;
          this tells you what to go and find.  Only the rows that are
          actually short render — a template listing everything the
          truck already has would be a second copy of the list above.
          Optional rows are included but said quietly: they are worth
          knowing and not worth chasing. */}
      {shortRows.length > 0 && (
        <p className="mt-2 text-2xs text-muted-foreground">
          Not aboard:{' '}
          {shortRows.map((r, n) => (
            <span key={r.category}>
              {n > 0 && ', '}
              <span className={r.required ? toneText('warn') : undefined}>
                {r.label || r.category}
                {r.short > 1 && ` ×${r.short}`}
                {!r.required && ' (optional)'}
              </span>
            </span>
          ))}
        </p>
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
    </Card>
  );
}
