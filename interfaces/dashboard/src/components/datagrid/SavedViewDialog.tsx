import { useEffect, useMemo, useRef, useState } from 'react';
import type { ColumnFiltersState } from '@tanstack/react-table';
import { Plus, X, SlidersHorizontal } from 'lucide-react';
import type { AnyColumn } from '../../types';
import { cn } from '../../lib/utils';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '../ui/dialog';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../ui/select';
import ColumnFilterMenu from './ColumnFilterMenu';
import { computeFacets, isFilterValueEmpty, viewIsEmpty } from './savedViews';

/**
 * Build-a-view dialog.  The whole view is defined HERE — a name plus
 * filter rows you add inline (pick a column, pick its value) — so an
 * operator never has to filter the grid first.  Any filters already
 * applied are pre-loaded so "capture what I'm looking at" still works.
 * Each row's value editor reuses ``ColumnFilterMenu`` (the same
 * select / range / date-range popover the column headers use).
 */

const emptyValueFor = (mode: string | undefined): unknown =>
  mode === 'range' || mode === 'date-range' ? [null, null] : [];

function summarize(col: AnyColumn, value: unknown, facet: { options: { value: string; label: string }[] }): string {
  if (isFilterValueEmpty(col.filterMode, value)) return 'Any value';
  if (col.filterMode === 'range') {
    const [a, b] = value as [number | null, number | null];
    return `${a ?? '−∞'} – ${b ?? '+∞'}${col.filterRange?.unit ? ' ' + col.filterRange.unit : ''}`;
  }
  if (col.filterMode === 'date-range') {
    const [a, b] = value as [string | null, string | null];
    return `${a ?? '…'} → ${b ?? '…'}`;
  }
  const vals = value as string[];
  const labels = vals.map(v => facet.options.find(o => o.value === v)?.label ?? v);
  return labels.length <= 2 ? labels.join(', ') : `${labels.slice(0, 2).join(', ')} +${labels.length - 2}`;
}

interface SavedViewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  columns: AnyColumn[];
  /** Full dataset — used to offer real values / bounds in the pickers. */
  data: Record<string, unknown>[];
  initialName?: string;
  initialFilters: ColumnFiltersState;
  initialSearch?: string;
  /** Title verb — "Save" for new, "Update" when editing an existing view. */
  saveLabel?: string;
  title?: string;
  onSave: (name: string, filters: ColumnFiltersState, search: string) => void;
}

export default function SavedViewDialog({
  open, onOpenChange, columns, data,
  initialName, initialFilters, initialSearch, saveLabel = 'Save view',
  title = 'New view', onSave,
}: SavedViewDialogProps) {
  const filterable = useMemo(() => columns.filter(c => c.filterable), [columns]);
  const facets = useMemo(() => computeFacets(filterable, data), [filterable, data]);
  const byKey = useMemo(() => new Map(filterable.map(c => [c.key, c])), [filterable]);

  const [name, setName] = useState('');
  const [draft, setDraft] = useState<ColumnFiltersState>([]);
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [pendingOpen, setPendingOpen] = useState<string | null>(null);
  const anchors = useRef<Record<string, HTMLButtonElement | null>>({});

  // Reset from the incoming state each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setName(initialName ?? '');
    setDraft(initialFilters.filter(f => byKey.has(f.id)));
    setSearch(initialSearch ?? '');
    setEditing(null);
    setPendingOpen(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Open a just-added row's editor only AFTER its anchor button is in
  // the DOM — refs attach during commit, before this effect runs, so
  // opening in the same tick as ``addRow`` would position the popover
  // against <body> instead of the row.
  useEffect(() => {
    if (pendingOpen && anchors.current[pendingOpen]) {
      setEditing(pendingOpen);
      setPendingOpen(null);
    }
  }, [pendingOpen]);

  const setRowValue = (id: string, value: unknown) =>
    setDraft(prev => prev.map(f => (f.id === id ? { ...f, value } : f)));
  const removeRow = (id: string) => setDraft(prev => prev.filter(f => f.id !== id));
  const addRow = (id: string) => {
    const col = byKey.get(id);
    if (!col) return;
    setDraft(prev => [...prev, { id, value: emptyValueFor(col.filterMode) }]);
    setPendingOpen(id);
  };

  const used = new Set(draft.map(f => f.id));
  const addable = filterable.filter(c => !used.has(c.key));

  // Save with the no-op rows stripped; block a view that constrains nothing.
  const cleaned = draft.filter(f => {
    const col = byKey.get(f.id);
    return col && !isFilterValueEmpty(col.filterMode, f.value);
  });
  const canSave = name.trim() !== '' && !viewIsEmpty(cleaned, search);

  const handleSave = () => {
    if (!canSave) return;
    onSave(name.trim(), cleaned, search.trim());
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Name it and choose the filters that define it. It becomes a personal
            tab — an isolated scope, just like Active / Archive.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div>
            <label htmlFor="view-name" className="block text-xs font-medium text-muted-foreground mb-1.5">
              View name
            </label>
            <Input
              id="view-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Cameras"
              autoFocus
            />
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground mb-1.5">Filters</div>
            {draft.length === 0 && (
              <p className="text-xs text-muted-foreground italic mb-2">
                No filters yet — add one below to define what this tab shows.
              </p>
            )}
            <div className="space-y-2">
              {draft.map((f) => {
                const col = byKey.get(f.id);
                if (!col) return null;
                const facet = facets[col.key] ?? { options: [], counts: {} };
                return (
                  <div key={f.id} className="flex items-center gap-2">
                    <span className="text-xs font-medium w-28 shrink-0 truncate">{col.label}</span>
                    <span className="text-2xs text-muted-foreground shrink-0">is</span>
                    <button
                      type="button"
                      ref={(el) => { anchors.current[f.id] = el; }}
                      onClick={() => setEditing(f.id)}
                      className={cn(
                        'flex-1 min-w-0 inline-flex items-center justify-between gap-2 px-2.5 h-8 rounded-md border text-xs text-left',
                        'border-input bg-transparent hover:bg-muted',
                        isFilterValueEmpty(col.filterMode, f.value)
                          ? 'text-muted-foreground border-dashed'
                          : 'text-foreground border-primary/40',
                      )}
                    >
                      <span className="truncate">{summarize(col, f.value, facet)}</span>
                      <SlidersHorizontal size={14} className="text-muted-foreground shrink-0" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeRow(f.id)}
                      aria-label={`Remove ${col.label} filter`}
                      className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted shrink-0"
                    >
                      <X size={14} />
                    </button>
                    {/* value editor — the same popover the column header uses */}
                    {col.filterMode === 'range' ? (
                      <ColumnFilterMenu
                        mode="range"
                        label={col.label}
                        bounds={{
                          min: facet.numeric?.min ?? 0,
                          max: facet.numeric?.max ?? 0,
                          step: col.filterRange?.step ?? 1,
                          unit: col.filterRange?.unit ?? '',
                        }}
                        value={(f.value as [number | null, number | null]) ?? [null, null]}
                        onChange={(next) => setRowValue(f.id, next)}
                        open={editing === f.id}
                        onOpenChange={(o) => setEditing(o ? f.id : null)}
                        anchor={anchors.current[f.id] ?? null}
                      />
                    ) : col.filterMode === 'date-range' ? (
                      <ColumnFilterMenu
                        mode="date-range"
                        label={col.label}
                        bounds={facet.dates ?? { min: '', max: '' }}
                        value={(f.value as [string | null, string | null]) ?? [null, null]}
                        onChange={(next) => setRowValue(f.id, next)}
                        open={editing === f.id}
                        onOpenChange={(o) => setEditing(o ? f.id : null)}
                        anchor={anchors.current[f.id] ?? null}
                      />
                    ) : (
                      <ColumnFilterMenu
                        label={col.label}
                        options={facet.options}
                        counts={facet.counts}
                        value={(f.value as string[]) ?? []}
                        onChange={(next) => setRowValue(f.id, next)}
                        open={editing === f.id}
                        onOpenChange={(o) => setEditing(o ? f.id : null)}
                        anchor={anchors.current[f.id] ?? null}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {addable.length > 0 && (
              <div className="mt-2">
                <Select value="" onValueChange={addRow}>
                  <SelectTrigger className="h-8 w-auto gap-2 text-xs text-primary border-dashed">
                    <Plus size={14} />
                    <SelectValue placeholder="Add filter" />
                  </SelectTrigger>
                  <SelectContent>
                    {addable.map((c) => (
                      <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={!canSave}>{saveLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
