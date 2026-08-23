import { useEffect, useMemo, useRef, useState } from 'react';
import type { ColumnFiltersState } from '@tanstack/react-table';
import { Plus, X, SlidersHorizontal, Search } from 'lucide-react';
import type { AnyColumn } from '../../../types';
import { cn } from '../../../lib/utils';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '../../ui/dialog';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '../../ui/select';
import ColumnFilterMenu from '../ColumnFilterMenu';
import { computeFacets, isFilterValueEmpty, tabIsEmpty } from './savedTabs';
import { TAB_ICONS, TAB_ICON_KEYS, TAB_ICON_TERMS } from './tabIcons';
import { Tip, InfoTip } from '../../tooltip';
import type { Tone } from '../../../lib/status';

/** Tone choices for the count-badge colour (order matches the picker). */
const TAB_TONES: Tone[] = ['info', 'ok', 'warn', 'danger', 'neutral'];
/** Solid swatch class per tone — the picker shows PURE COLOUR (the count
 *  badge itself renders the softer ``toneClasses`` tint).  Written out
 *  literally because Tailwind can't see interpolated class names. */
const TONE_SWATCH: Record<Tone, string> = {
  info:    'bg-info',
  ok:      'bg-ok',
  warn:    'bg-warn',
  danger:  'bg-danger',
  neutral: 'bg-muted-foreground',
};
/** Plain colour names — the honest label for a colour choice. */
const TONE_LABEL: Record<Tone, string> = {
  info: 'Blue', ok: 'Green', warn: 'Amber', danger: 'Red', neutral: 'Grey',
};

/**
 * Build-a-tab dialog.  The whole tab is defined HERE — a name plus
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

interface SavedTabDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  columns: AnyColumn[];
  /** Full dataset — used to offer real values / bounds in the pickers. */
  data: Record<string, unknown>[];
  initialName?: string;
  initialFilters: ColumnFiltersState;
  initialSearch?: string;
  initialTone?: Tone;
  initialIcon?: string;
  /** Title verb — "Save" for new, "Update" when editing an existing tab. */
  saveLabel?: string;
  title?: string;
  /** Human note for the sort the tab will also carry (e.g. "Sorted by
   *  Priority ↑"), shown read-only so the operator knows it's captured. */
  capturedSort?: string;
  onSave: (
    name: string, filters: ColumnFiltersState, search: string,
    tone?: Tone, icon?: string,
  ) => void;
}

export default function SavedTabDialog({
  open, onOpenChange, columns, data,
  initialName, initialFilters, initialSearch, initialTone, initialIcon,
  saveLabel = 'Save tab',
  title = 'New tab', capturedSort, onSave,
}: SavedTabDialogProps) {
  const filterable = useMemo(() => columns.filter(c => c.filterable), [columns]);
  const facets = useMemo(() => computeFacets(filterable, data), [filterable, data]);
  const byKey = useMemo(() => new Map(filterable.map(c => [c.key, c])), [filterable]);

  const [name, setName] = useState('');
  const [draft, setDraft] = useState<ColumnFiltersState>([]);
  const [search, setSearch] = useState('');
  const [tone, setTone] = useState<Tone | undefined>(undefined);
  const [icon, setIcon] = useState<string | undefined>(undefined);
  const [iconQuery, setIconQuery] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [pendingOpen, setPendingOpen] = useState<string | null>(null);
  const anchors = useRef<Record<string, HTMLButtonElement | null>>({});

  // Reset from the incoming state each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setName(initialName ?? '');
    setDraft(initialFilters.filter(f => byKey.has(f.id)));
    setSearch(initialSearch ?? '');
    setTone(initialTone);
    setIcon(initialIcon);
    setIconQuery('');
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

  // Icon search — matches the key AND its synonym terms ("gas" → fuel,
  // "wheel" → tire), so a user finds an icon by what they call it.
  const shownIcons = useMemo(() => {
    const q = iconQuery.trim().toLowerCase();
    if (!q) return TAB_ICON_KEYS;
    return TAB_ICON_KEYS.filter(k =>
      k.includes(q) || (TAB_ICON_TERMS[k] ?? '').includes(q));
  }, [iconQuery]);

  // Save with the no-op rows stripped; block a tab that constrains nothing.
  const cleaned = draft.filter(f => {
    const col = byKey.get(f.id);
    return col && !isFilterValueEmpty(col.filterMode, f.value);
  });
  const canSave = name.trim() !== '' && !tabIsEmpty(cleaned, search);

  const handleSave = () => {
    if (!canSave) return;
    onSave(name.trim(), cleaned, search.trim(), tone, icon);
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

        {/* The FORM itself never scrolls — it stays a compact, fixed-size
            dialog.  The only scroll region is the icon panel below, which
            is capped so the long icon set can't stretch the dialog. */}
        <div className="space-y-4 py-1">
          <div>
            <label htmlFor="tab-name" className="block text-xs font-medium text-muted-foreground mb-1.5">
              Tab name
            </label>
            <Input
              id="tab-name"
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
                      <SlidersHorizontal className="text-muted-foreground shrink-0 size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeRow(f.id)}
                      aria-label={`Remove ${col.label} filter`}
                      className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted shrink-0"
                    >
                      <X className="size-3.5" />
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
                    <Plus className="size-3.5" />
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

          {/* Appearance — personal recognition only; no effect on scope.
              Explanations live behind ⓘ (learn-once helper text rule). */}
          <div className="border-t border-border pt-3 space-y-3">
            <div className="text-xs font-medium text-muted-foreground inline-flex items-center gap-1">
              Appearance
              <InfoTip
                size={12}
                label="Optional. A colour and icon make this tab easier to spot in the strip — neither changes what the tab shows."
              />
            </div>
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1.5 inline-flex items-center gap-1">
                Colour
                <InfoTip size={12} label="Tints the count badge only — not the whole tab." />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {/* None → the default (untinted) count badge. */}
                <Tip label="No colour">
                  <button
                    type="button"
                    onClick={() => setTone(undefined)}
                    aria-label="No colour"
                    aria-pressed={tone === undefined}
                    className={cn(
                      'size-6 rounded-full border border-dashed border-muted-foreground/60 transition min-h-tap min-w-tap',
                      tone === undefined
                        ? 'ring-2 ring-foreground ring-offset-2 ring-offset-background'
                        : 'hover:border-ring',
                    )}
                  />
                </Tip>
                {TAB_TONES.map((t) => (
                  <Tip key={t} label={TONE_LABEL[t]}>
                    <button
                      type="button"
                      onClick={() => setTone(t)}
                      aria-label={TONE_LABEL[t]}
                      aria-pressed={tone === t}
                      className={cn(
                        'size-6 rounded-full transition min-h-tap min-w-tap', TONE_SWATCH[t],
                        tone === t
                          ? 'ring-2 ring-foreground ring-offset-2 ring-offset-background'
                          : 'hover:opacity-80',
                      )}
                    />
                  </Tip>
                ))}
              </div>
            </div>
            <div>
              {/* "None" CLEARS the choice — it's an action, not a member of
                  the icon set, so it lives on the label row rather than as
                  an odd-shaped tile inside the grid.  The count tells the
                  operator how much is in the panel (and how much a search
                  narrowed it). */}
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-xs font-medium text-muted-foreground inline-flex items-center gap-1">
                  Icon
                  <InfoTip size={12} label="Shows before the tab name, in place of the dot." />
                  <span className="font-normal">
                    · {iconQuery.trim()
                        ? `${shownIcons.length} of ${TAB_ICON_KEYS.length}`
                        : TAB_ICON_KEYS.length}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setIcon(undefined)}
                  aria-pressed={icon === undefined}
                  className={cn(
                    'h-6 px-2 rounded-md border text-2xs transition min-h-tap',
                    icon === undefined
                      ? 'border-foreground text-foreground'
                      : 'border-border text-muted-foreground hover:border-ring',
                  )}
                >
                  None
                </button>
              </div>
              <div className="relative mb-1.5">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none size-3.5" />
                <Input
                  value={iconQuery}
                  onChange={(e) => setIconQuery(e.target.value)}
                  placeholder="Search icons"
                  className="h-8 pl-7 text-xs"
                  aria-label="Search icons"
                />
              </div>
              {/* The icon set lives in its OWN bordered panel with its own
                  scroll — the dialog stays a fixed, compact size while the
                  whole set stays browsable (the common case when you don't
                  know an icon's name; search is the shortcut, not the only
                  path).  This is also the shape that scales if the set ever
                  grows to lucide's full ~1,700. */}
              <div className="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto rounded-md border border-border bg-muted/20 p-2">
                {shownIcons.map((key) => {
                  const Icon = TAB_ICONS[key];
                  return (
                    <Tip key={key} label={key}>
                      <button
                        type="button"
                        onClick={() => setIcon(key)}
                        aria-label={`Icon ${key}`}
                        aria-pressed={icon === key}
                        className={cn(
                          // Same selected-state language as the colour
                          // swatches: a ring, not a border-colour swap.
                          'size-7 rounded-md border inline-flex items-center justify-center transition min-h-tap min-w-tap',
                          icon === key
                            ? 'border-transparent bg-primary/10 text-foreground ring-2 ring-foreground ring-offset-2 ring-offset-background'
                            : 'border-border text-muted-foreground hover:border-ring',
                        )}
                      >
                        <Icon className="size-3.5" />
                      </button>
                    </Tip>
                  );
                })}
                {shownIcons.length === 0 && (
                  <p className="text-2xs text-muted-foreground italic py-1.5">
                    No icons match “{iconQuery.trim()}”.
                  </p>
                )}
              </div>
            </div>
          </div>

          {capturedSort && (
            <p className="text-2xs text-muted-foreground border-t border-border pt-3">
              Also opens with your current sort — <span className="text-foreground font-medium">{capturedSort}</span>.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={!canSave}>{saveLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
