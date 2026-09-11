/**
 * What a vehicle type is SUPPOSED to carry.
 *
 * Inventory could only report what somebody had recorded, which leaves
 * its strongest case unmakeable: a dashcam nobody ever typed in cannot go
 * missing — it simply is not there, and nothing notices.  This is where
 * the expectation gets declared, so that absence becomes a fact.
 *
 * Rows are keyed on CATEGORY, not on a name.  Labels are free text
 * somebody types standing at a truck ("front dashcam", "dash cam"), and a
 * template keyed on those would drift the day it shipped — so a row says
 * "two of `camera`", and its label is for the reader.
 *
 * Shaped after the PTI checklist editor next door: same truck/trailer
 * split, same reset-to-standard escape, because it is the same job on the
 * same registry and learning it twice is a cost with no return.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, RotateCcw, Trash2 } from '../../lib/icons';

import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Checkbox } from '../../components/ui/checkbox';
import { CardSkeleton, ErrorState } from '../../components/shell';
import { categoryMeta } from './categories';

type VehicleType = 'truck' | 'trailer';

interface ExpectedRow {
  category: string;
  label: string;
  quantity: number;
  required: boolean;
  sort_order: number;
}

interface ExpectedResponse {
  /** ONE catalogue for the account — what a vehicle carries is a fact
   *  about the vehicle, so every role reads the same list. */
  catalogue: Record<VehicleType, ExpectedRow[]>;
  standard: Record<VehicleType, ExpectedRow[]>;
  vehicle_types: VehicleType[];
  /** The caller's own role, and what it goes red about.  `null` means it
   *  has never narrowed — flagged on everything. */
  role: string;
  focus: string[] | null;
}

const TABS: { key: VehicleType; label: string }[] = [
  { key: 'truck', label: 'Trucks' },
  { key: 'trailer', label: 'Trailers' },
];

export default function ExpectedEditor({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const [type, setType] = useState<VehicleType>('truck');
  const [draft, setDraft] = useState<ExpectedRow[] | null>(null);

  const { data, isLoading, error } = useQuery<ExpectedResponse>({
    queryKey: ['inventory-expected'],
    queryFn: () => apiJSON('/inventory/expected'),
  });

  // The draft follows the server until somebody edits, and resets when
  // the tab changes — an edit to the truck list must not follow the
  // reader over to trailers.
  useEffect(() => { setDraft(null); }, [type]);
  const rows = draft ?? data?.catalogue?.[type] ?? [];
  const dirty = draft !== null;

  const save = useMutation({
    mutationFn: (items: ExpectedRow[]) =>
      apiJSON('/inventory/expected', {
        method: 'PUT',
        body: { vehicle_type: type, items },
      }),
    onSuccess: () => {
      setDraft(null);
      void qc.invalidateQueries({ queryKey: ['inventory-expected'] });
      // Every truck's card reads its coverage from this — leaving them on
      // the old answer would show a completeness nobody expects any more.
      void qc.invalidateQueries({ queryKey: ['vehicle-inventory'] });
    },
  });

  // The second scope.  `null` focus means "flagged on everything", so a
  // role that has never narrowed keeps every box ticked until it does.
  const [focusDraft, setFocusDraft] = useState<string[] | null>(null);
  const allCategories = Object.values(data?.catalogue ?? {})
    .flat()
    .map((r) => r.category)
    .filter((c, i, a) => a.indexOf(c) === i);
  const focus = focusDraft ?? data?.focus ?? allCategories;
  const focusDirty = focusDraft !== null;

  const saveFocus = useMutation({
    mutationFn: (categories: string[]) =>
      apiJSON('/inventory/expected/focus', {
        method: 'PUT',
        body: { role: data?.role, categories },
      }),
    onSuccess: () => {
      setFocusDraft(null);
      void qc.invalidateQueries({ queryKey: ['inventory-expected'] });
      void qc.invalidateQueries({ queryKey: ['vehicle-inventory'] });
    },
  });

  const edit = (i: number, patch: Partial<ExpectedRow>) =>
    setDraft(rows.map((r, n) => (n === i ? { ...r, ...patch } : r)));

  if (isLoading) return <CardSkeleton />;
  if (error) return <ErrorState title="Could not load the template" />;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 rounded-lg bg-muted p-1 w-fit">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setType(t.key)}
            className={`px-3 py-1.5 text-sm rounded-md transition ${
              type === t.key
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className="text-sm text-muted-foreground max-w-2xl">
        Every {type === 'truck' ? 'truck' : 'trailer'} is measured against this
        list. A vehicle short a <strong>required</strong> row is reported
        short on its card, even if nobody ever recorded the item — which is
        the whole point: a dashcam that was never typed in cannot go missing
        on its own.
      </p>

      {rows.length === 0 ? (
        /* A blank editor asks somebody to invent a list.  The standard one
           is the answer most fleets want, so it is offered as the action
           rather than mentioned as an option beside "add a row" — the
           recommendation leads, and inventing your own stays one press
           away. */
        <div className="rounded-lg border border-dashed p-5 text-center">
          <p className="text-sm text-muted-foreground max-w-md mx-auto">
            Nothing is expected on {type === 'truck' ? 'trucks' : 'trailers'} yet,
            so no {type} is ever reported short.
          </p>
          {canManage && (
            <Button
              className="mt-3"
              size="sm"
              onClick={() => setDraft((data?.standard?.[type] ?? []).map((r) => ({ ...r })))}
            >
              <RotateCcw /> Start from the standard list
            </Button>
          )}
        </div>
      ) : (
        <ul className="divide-y rounded-lg border">
          {rows.map((r, i) => (
            <li key={`${r.category}-${i}`} className="flex items-center gap-3 p-2.5">
              {/* The stable key, so it gets the example.  A blank row with
                  no placeholder asks for a value without saying what kind. */}
              <Input
                aria-label="Category"
                className="w-48"
                placeholder="camera, eld, fuel_card…"
                value={r.category}
                disabled={!canManage}
                onChange={(e) => edit(i, { category: e.target.value })}
              />
              <Input
                aria-label="What to call it"
                className="flex-1 min-w-0"
                placeholder={categoryMeta(r.category).label || 'What to call it'}
                value={r.label}
                disabled={!canManage}
                onChange={(e) => edit(i, { label: e.target.value })}
              />
              <Input
                aria-label="How many"
                type="number"
                min={1}
                max={99}
                className="w-20"
                value={r.quantity}
                disabled={!canManage}
                onChange={(e) => edit(i, { quantity: Math.max(1, Number(e.target.value) || 1) })}
              />
              {/* Declared and not enforced.  A toll transponder is normal
                  to carry and normal not to; flagging every truck without
                  one teaches people to ignore the flag, which costs more
                  than the flag is worth. */}
              <label className="flex items-center gap-1.5 text-sm text-muted-foreground whitespace-nowrap">
                <Checkbox
                  checked={r.required}
                  disabled={!canManage}
                  onChange={(e) => edit(i, { required: e.target.checked })}
                />
                Required
              </label>
              {canManage && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove ${r.label || r.category}`}
                  onClick={() => setDraft(rows.filter((_, n) => n !== i))}
                >
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setDraft([
                ...rows,
                { category: '', label: '', quantity: 1, required: true, sort_order: rows.length + 1 },
              ])
            }
          >
            <Plus /> Add a row
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDraft((data?.standard?.[type] ?? []).map((r) => ({ ...r })))}
          >
            <RotateCcw /> Reset to standard
          </Button>
          <div className="flex-1" />
          {dirty && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>
              Discard
            </Button>
          )}
          <Button
            size="sm"
            /* Disabled WITH A REASON — a grey button that will not say what
               it is waiting for is a dead end. */
            title={
              !dirty ? 'Nothing has changed yet'
                : rows.some((r) => !r.category.trim()) ? 'Every row needs a category'
                : 'Save this template'
            }
            disabled={!dirty || save.isPending || rows.some((r) => !r.category.trim())}
            onClick={() => save.mutate(rows.map((r, i) => ({ ...r, sort_order: i + 1 })))}
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      )}

      {/* ── the ROLE scope ──────────────────────────────────────────
          What a vehicle carries is one truth; which of it turns red is
          not.  The owner named the problem: something showing red for
          one role pulls a second role's focus onto what is not theirs —
          safety going red about dispatch's straps costs safety the thing
          red is for.  The count above stays identical for everybody. */}
      {allCategories.length > 0 && (
        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium">What my role goes red about</h3>
          <p className="text-sm text-muted-foreground max-w-2xl">
            Every role reads the same list above — this only decides which of
            it is flagged on <strong>your</strong> screens. Untick a category
            and your role stops being pulled toward it; the vehicle still owes
            it, and the roles that ticked it still see it.
          </p>
          <div className="flex flex-wrap gap-x-5 gap-y-2 pt-1">
            {allCategories.map((c) => (
              <label key={c} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={focus.includes(c)}
                  onChange={(e) =>
                    setFocusDraft(
                      e.target.checked ? [...focus, c] : focus.filter((x) => x !== c),
                    )
                  }
                />
                {categoryMeta(c).label}
              </label>
            ))}
          </div>
          {focusDirty && (
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" disabled={saveFocus.isPending}
                      onClick={() => saveFocus.mutate(focus)}>
                {saveFocus.isPending ? 'Saving…' : 'Save my focus'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setFocusDraft(null)}>
                Discard
              </Button>
            </div>
          )}
          {saveFocus.isSuccess && !focusDirty && (
            <p className="text-sm text-muted-foreground">
              Saved. Your screens stop flagging what you unticked.
            </p>
          )}
        </div>
      )}

      {save.isSuccess && !dirty && (
        <p className="text-sm text-muted-foreground">
          Saved. Every {type}'s card now measures against this list.
        </p>
      )}

      {save.isError && (
        <p className="text-sm text-destructive">
          {save.error instanceof Error ? save.error.message : 'Could not save the template'}
        </p>
      )}
    </div>
  );
}
