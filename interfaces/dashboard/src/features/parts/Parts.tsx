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
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Check, Cog, Globe, Plus, Wand2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import DataGrid from '../../components/datagrid';
import { PageHeader, EmptyState, ErrorState, TableSkeleton } from '../../components/shell';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import type { CatalogPart, PublicPartEntry, AnyColumn } from '../../types';

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
    aggregable: true, aggFns: ['sum', 'avg', 'max'],
    render: (v) => <span className="tabular-nums">{String(v ?? 0)}</span>,
  },
  {
    key: 'total_spent', label: 'Total Spent', sortable: true,
    aggregable: true, aggFns: ['sum', 'avg', 'max'],
    aggFormat: (value) => money(value),
    render: (v) => <span className="tabular-nums font-medium">{money(v)}</span>,
  },
  {
    key: 'notes', label: 'Notes', sortable: false,
    render: (v) => (v
      ? <span className="text-muted-foreground truncate max-w-sm inline-block align-bottom">{String(v)}</span>
      : <span className="text-muted-foreground">—</span>),
  },
];

// Public tab: canonical identities curated on the platform.  Identity
// only — linking happens automatically by name, or from a part's
// "Resolve as duplicate" dialog; there is no ceremony here.
const publicColumns: AnyColumn[] = [
  {
    key: 'name', label: 'Part', sortable: true,
    render: (v, row) => {
      const r = row as unknown as PublicPartEntry;
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
    key: 'category', label: 'Category', sortable: true, filterable: true,
    render: (v) => (v ? String(v) : <span className="text-muted-foreground">—</span>),
  },
  {
    key: 'description', label: 'Description', sortable: false,
    render: (v) => (v
      ? <span className="text-muted-foreground truncate max-w-sm inline-block align-bottom">{String(v)}</span>
      : <span className="text-muted-foreground">—</span>),
  },
  {
    // Populated only when market intel is live AND this account
    // shares (give-to-get) — otherwise the server omits the fields
    // and the column reads "—" for everyone.
    key: 'est_p25', label: 'Est. Price', sortable: true,
    render: (v, row) => {
      const r = row as unknown as PublicPartEntry;
      return (r.est_p25 != null && r.est_p75 != null) ? (
        <span className="tabular-nums font-medium">
          {money(r.est_p25)}–{money(r.est_p75)}
        </span>
      ) : <span className="text-muted-foreground">—</span>;
    },
  },
  {
    key: 'linked_part_name', label: 'Your Part', sortable: true,
    render: (v) => (v ? (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${toneClasses('ok')}`}>
        {String(v)}
      </span>
    ) : <span className="text-muted-foreground">—</span>),
  },
];

const EMPTY_FORM = { name: '', part_number: '', notes: '' };

export default function Parts() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<'mine' | 'public'>('mine');

  // Assembly vocabulary (level 2 of System→Assembly→Part) — fetched,
  // never hardcoded (the vocabulary-drift rule).
  const { data: asmData } = useQuery<{ assemblies: Array<{ key: string; label: string; system_key: string }> }>({
    queryKey: ['part-assemblies'],
    queryFn: () => apiJSON('/parts/assemblies'),
    staleTime: 5 * 60_000,
  });
  const asmLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of asmData?.assemblies ?? []) m.set(a.key, a.label);
    return m;
  }, [asmData]);

  // Bulk-confirm: one click applies every visible suggestion (only
  // onto blank rows — a hand-assigned assembly is never overwritten).
  const applyAll = useMutation({
    mutationFn: () => apiJSON<{ applied: number }>(
      '/parts/apply-assembly-suggestions', { method: 'POST' }),
    onSuccess: (r) => {
      toast.success(`Assembly set on ${r.applied} part${r.applied === 1 ? '' : 's'}`);
      qc.invalidateQueries({ queryKey: ['parts-catalog'] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const { data, isLoading, error } = useQuery<{ parts: CatalogPart[] }>({
    queryKey: ['parts-catalog'],
    queryFn: () => apiJSON<{ parts: CatalogPart[] }>('/parts'),
  });
  const parts = useMemo(() => data?.parts ?? [], [data]);

  // My-parts columns + the Assembly column (label from the fetched
  // vocabulary; suggest-confirm chip on blanks — one click applies,
  // nothing happens without it).
  const myColumns = useMemo<AnyColumn[]>(() => {
    const assemblyCol: AnyColumn = {
      key: 'assembly_key', label: 'Assembly', sortable: true, filterable: true,
      filterValue: (row) => String((row as unknown as CatalogPart).assembly_key ?? ''),
      render: (v, row) => {
        const part = row as unknown as CatalogPart & { suggested_assembly?: string };
        if (v) return <span className="text-sm">{asmLabel.get(String(v)) ?? String(v)}</span>;
        if (part.suggested_assembly) {
          return (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                apiJSON(`/parts/${part.id}`, {
                  method: 'PUT',
                  body: { assembly_key: part.suggested_assembly },
                })
                  .then(() => {
                    toast.success('Assembly set');
                    qc.invalidateQueries({ queryKey: ['parts-catalog'] });
                  })
                  .catch((err) => toast.error(err instanceof Error ? err.message : 'Failed'));
              }}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs font-medium transition hover:brightness-110 ${toneClasses('info')} min-h-tap`}
            >
              <Check className="size-3" aria-hidden />
              {asmLabel.get(part.suggested_assembly) ?? part.suggested_assembly}?
            </button>
          );
        }
        return <span className="text-muted-foreground">—</span>;
      },
    };
    const cols = [...partColumns];
    cols.splice(2, 0, assemblyCol);   // after Part name
    return cols;
  }, [asmLabel, qc]);

  const suggestionCount = useMemo(
    () => parts.filter((pt) =>
      !(pt as CatalogPart & { suggested_assembly?: string }).assembly_key
      && (pt as CatalogPart & { suggested_assembly?: string }).suggested_assembly,
    ).length,
    [parts],
  );


  const { data: pubData, isLoading: pubLoading, error: pubError } = useQuery<{ entries: PublicPartEntry[] }>({
    queryKey: ['parts-public-browse'],
    queryFn: () => apiJSON<{ entries: PublicPartEntry[] }>('/parts/public/browse'),
    enabled: tab === 'public',
  });
  const pubEntries = pubData?.entries ?? [];

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
        description="Every part your invoices mention, deduplicated into one catalog. Open a part to see which trucks keep needing it and what each shop charges. Assembly is level 2 of System → Assembly → Part — it groups a part inside its system so Cost Reports can break a system's bar down."
        actions={(
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus /> Add part
          </Button>
        )}
      />

      <div role="tablist" aria-label="Part sections" className="flex gap-1 mb-4 border-b border-border">
        {([
          { key: 'mine' as const, label: 'My parts' },
          { key: 'public' as const, label: 'Shared' },
        ]).map(({ key, label }) => {
          const sel = tab === key;
          return (
            <button
              key={key}
              role="tab"
              aria-selected={sel}
              onClick={() => setTab(key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                sel
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {tab === 'mine' ? (
        error ? (
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
            columns={myColumns}
            headerToolbar={suggestionCount > 0 ? (
              <Button
                size="sm" variant="outline"
                disabled={applyAll.isPending}
                onClick={() => applyAll.mutate()}
              >
                <Wand2 />
                {applyAll.isPending
                  ? 'Applying…'
                  : `Apply ${suggestionCount} suggested assembl${suggestionCount === 1 ? 'y' : 'ies'}`}
              </Button>
            ) : undefined}
            data={parts as unknown as Record<string, unknown>[]}
            searchKey={['name', 'part_number', 'notes']}
            searchPlaceholder="Search parts…"
            onRowClick={(row) => navigate(`/parts/${(row as unknown as CatalogPart).id}`)}
          />
        )
      ) : (
        pubError ? (
          <ErrorState message={pubError instanceof Error ? pubError.message : 'Failed to load the catalog'} />
        ) : pubLoading ? (
          <TableSkeleton rows={8} cols={4} />
        ) : pubEntries.length === 0 ? (
          <EmptyState
            icon={Globe}
            title="The catalog is just getting started"
            description="Platform-curated canonical parts appear here as they're added. Your parts link to them automatically by name — no action needed."
          />
        ) : (
          <DataGrid
            tableId="parts-public-browse"
            columns={publicColumns}
            data={pubEntries as unknown as Record<string, unknown>[]}
            searchKey={['name', 'category', 'part_number', 'description']}
            searchPlaceholder="Search the catalog…"
            onRowClick={(row) => {
              const e = row as unknown as PublicPartEntry;
              if (e.linked_part_id) {
                navigate(`/parts/${e.linked_part_id}`);
              } else {
                toast.info('Not one of your parts yet — link a part to this entry from its page: Merge into… → Catalog.');
              }
            }}
          />
        )
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
