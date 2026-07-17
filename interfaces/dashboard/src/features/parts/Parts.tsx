/**
 * Parts — the per-account parts master data (features/parts).
 *
 * The catalog collects ITSELF: every work-order line and Datatruck
 * sync resolves its part name into the catalog (alias-aware), so this
 * page is a lens over invoice truth, not a data-entry chore.  Add part
 * exists for the "before its first invoice" case and uses resolve
 * semantics — an existing name opens the existing part, honestly.
 * Rows drill into the part profile (recurrence per vehicle, price per
 * vendor, purchase history); dedup/merge and edits live there too.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Cog, Plus } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/DataGrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import type { CatalogPart, AnyColumn } from '../../types';

function money(v: unknown): string {
  return `$${Number(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const partColumns: AnyColumn[] = [
  {
    // Identifier, not a sequence — the global serial means per-account
    // IDs aren't contiguous.  Hidden by default; operators who need it
    // (merge bookkeeping, support) unhide via the Columns popover.
    key: 'id', label: 'ID', sortable: true, defaultHidden: true,
    render: (v) => <span className="font-mono text-xs text-muted-foreground">#{String(v)}</span>,
  },
  {
    key: 'name', label: 'Part', sortable: true,
    render: (v, row) => {
      const r = row as unknown as CatalogPart;
      return (
        <span className="inline-flex items-center gap-2">
          <span className="font-medium">{String(v)}</span>
          {r.part_number && (
            <span className="font-mono text-2xs text-muted-foreground">{r.part_number}</span>
          )}
        </span>
      );
    },
  },
  {
    key: 'usage_count', label: 'Uses', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
  },
  {
    key: 'total_spent', label: 'Total Spent', sortable: true,
    render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
  },
  {
    key: 'notes', label: 'Notes', sortable: false,
    render: (v) => (v
      ? <span className="text-muted-foreground truncate max-w-sm inline-block align-bottom">{String(v)}</span>
      : <span className="text-muted-foreground">—</span>),
  },
];

const EMPTY_FORM = { name: '', part_number: '', notes: '' };

export default function Parts() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const { data, isLoading, error } = useQuery<{ parts: CatalogPart[] }>({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON<{ parts: CatalogPart[] }>('/parts'),
  });
  const parts = data?.parts ?? [];

  const addPart = async () => {
    if (!form.name.trim()) { toast.error('Name is required'); return; }
    setSaving(true);
    try {
      const res = await apiJSON<{ part: CatalogPart; created: boolean }>('/parts', {
        method: 'POST',
        body: JSON.stringify(form),
      });
      setAddOpen(false);
      setForm(EMPTY_FORM);
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
      if (res.created) {
        toast.success('Part added');
      } else {
        // Honest resolve: the row existed; typed details were NOT applied.
        toast.info('Already in your catalog — opened it. Your typed details were not applied.');
      }
      navigate(`/parts/${res.part.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add part');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        icon={Cog}
        title="Parts"
        description="Every part your invoices mention, deduplicated into one catalog. Open a part to see which trucks keep needing it and what each shop charges."
        actions={(
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add part
          </Button>
        )}
      />

      {error ? (
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load parts'} />
      ) : isLoading ? (
        <TableSkeleton rows={8} cols={4} />
      ) : parts.length === 0 ? (
        <EmptyState
          icon={Cog}
          title="No parts yet"
          description="Parts appear here automatically as work-order line items are saved — or add one ahead of its first invoice."
        />
      ) : (
        <DataGrid
          tableId="parts-catalog"
          columns={partColumns}
          data={parts as unknown as Record<string, unknown>[]}
          searchKey={['name', 'part_number', 'notes']}
          searchPlaceholder="Search parts…"
          onRowClick={(row) => navigate(`/parts/${(row as unknown as CatalogPart).id}`)}
        />
      )}

      {/* Add-part dialog — resolve semantics, same contract as Add vendor. */}
      <Dialog open={addOpen} onOpenChange={(o) => { if (!o) setAddOpen(false); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Add part</DialogTitle>
            <DialogDescription>
              Create a catalog part before its first invoice — work-order
              lines under this name will link to it automatically. If the
              name already exists, you'll be taken to it.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2.5">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-add-name">Name</label>
            <input
              id="part-add-name"
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-add-number">Part number (optional)</label>
            <input
              id="part-add-number"
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground font-mono"
              value={form.part_number}
              onChange={(e) => setForm((f) => ({ ...f, part_number: e.target.value }))}
            />
            <label className="text-xs font-medium text-muted-foreground" htmlFor="part-add-notes">Notes (optional)</label>
            <textarea
              id="part-add-notes"
              rows={3}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button onClick={addPart} disabled={saving}>{saving ? 'Adding…' : 'Add part'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
