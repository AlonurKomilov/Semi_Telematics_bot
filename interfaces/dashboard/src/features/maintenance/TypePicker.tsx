/**
 * Maintenance task-type picker with inline custom-type creation.
 *
 * Renders a dropdown whose options are:
 *   1. The built-in type list (Oil Change, Tire Service, etc.)
 *   2. A divider, then the account's saved custom types (sorted by
 *      label, fetched from ``GET /maintenance/task-types``).
 *   3. A sentinel ``+ Add custom type…`` entry at the bottom.
 *
 * When the operator selects the sentinel, an inline text input
 * replaces the dropdown.  Hitting Enter or clicking Save:
 *   • POSTs to ``/maintenance/task-types`` (idempotent; the backend
 *     returns the existing row if the label was added before).
 *   • Invalidates the React-Query cache so every TypePicker mounted
 *     on the page (add form + edit drawer + templates modal) sees
 *     the new option immediately.
 *   • Sets the dropdown to the newly created type so the form
 *     proceeds without an extra click.
 *
 * Used by both the add-task form and the edit-task drawer on the
 * Maintenance page.  The Templates modal can adopt it later.
 */

import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X, Loader2 } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { TASK_TYPE_OPTIONS } from './badges';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
  SelectGroup, SelectLabel,
} from '../../components/ui/select';

// Sentinel value that triggers the inline-add UI.  Chosen so it
// can never collide with a real type value (server-side custom
// types are namespaced as ``custom_<slug>``; built-ins use bare
// short keys).
export const ADD_NEW_SENTINEL = '__add_new__';

export interface CustomTaskType {
  id: number;
  account_id: number;
  value: string;
  label: string;
  created_by: number;
  created_at: string;
  updated_at: string;
}

interface TypePickerProps {
  value: string;
  onChange: (next: string) => void;
  className?: string;
  /** Whether the operator has write permission.  Drives whether
   *  the ``+ Add custom type…`` option appears and whether the
   *  inline create input is rendered.  Defaults to true so callers
   *  don't have to thread the permission everywhere. */
  canCreate?: boolean;
}

async function fetchCustomTypes(): Promise<{ types: CustomTaskType[] }> {
  return apiJSON<{ types: CustomTaskType[] }>('/maintenance/task-types');
}

async function createCustomType(label: string): Promise<CustomTaskType> {
  return apiJSON<CustomTaskType>('/maintenance/task-types', {
    method: 'POST',
    body: { label },
  });
}

export default function TypePicker({
  value,
  onChange,
  className,
  canCreate = true,
}: TypePickerProps) {
  const qc = useQueryClient();
  const [inlineAdding, setInlineAdding] = useState(false);
  const [draftLabel, setDraftLabel] = useState('');
  const [draftError, setDraftError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-custom-types'],
    queryFn: fetchCustomTypes,
    staleTime: 60_000,
  });

  const createMutation = useMutation({
    mutationFn: createCustomType,
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ['maintenance-custom-types'] });
      // Switch the dropdown to the just-created type immediately so
      // the form can proceed without the operator picking it again.
      onChange(row.value);
      setInlineAdding(false);
      setDraftLabel('');
      setDraftError('');
    },
    onError: (err: unknown) => {
      setDraftError(err instanceof Error ? err.message : String(err));
    },
  });

  // Focus the input when entering inline-add mode.
  useEffect(() => {
    if (inlineAdding) inputRef.current?.focus();
  }, [inlineAdding]);

  const customTypes = data?.types ?? [];

  // Filter the built-in list to drop the legacy "custom" sentinel —
  // we replace it with the per-account list + the new ``Add custom
  // type…`` entry at the bottom.
  const builtInOptions = TASK_TYPE_OPTIONS.filter((o) => o.value !== 'custom');

  const handleSelectChange = (next: string) => {
    if (next === ADD_NEW_SENTINEL) {
      if (!canCreate) return;
      setInlineAdding(true);
      setDraftLabel('');
      setDraftError('');
      return;
    }
    onChange(next);
  };

  const submitDraft = () => {
    const trimmed = draftLabel.trim();
    if (!trimmed) {
      setDraftError('Label cannot be empty');
      return;
    }
    if (createMutation.isPending) return;
    createMutation.mutate(trimmed);
  };

  const cancelDraft = () => {
    setInlineAdding(false);
    setDraftLabel('');
    setDraftError('');
  };

  if (inlineAdding) {
    return (
      <div>
        <div className="flex items-stretch gap-1">
          <input
            ref={inputRef}
            type="text"
            value={draftLabel}
            onChange={(e) => setDraftLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submitDraft();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                cancelDraft();
              }
            }}
            maxLength={60}
            placeholder="e.g. Tire change"
            className={`flex-1 bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring ${className ?? ''}`}
          />
          <button
            type="button"
            onClick={submitDraft}
            disabled={createMutation.isPending || !draftLabel.trim()}
            className="flex items-center gap-1 text-xs px-2 py-1 bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Save (Enter)"
          >
            {createMutation.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Check size={12} />
            )}
            Save
          </button>
          <button
            type="button"
            onClick={cancelDraft}
            disabled={createMutation.isPending}
            className="flex items-center gap-1 text-xs px-2 py-1 border border-border rounded hover:bg-muted disabled:opacity-50"
            title="Cancel (Esc)"
          >
            <X size={12} />
          </button>
        </div>
        {draftError && (
          <p className="text-2xs text-danger mt-1">{draftError}</p>
        )}
        <p className="text-2xs text-muted-foreground mt-1">
          Saved to your account — every user on this account will see it.
        </p>
      </div>
    );
  }

  // Resolve the current selection's display label.  If the value
  // matches one of the custom types but the list hasn't loaded yet,
  // fall back to the raw value so the dropdown doesn't visually
  // "reset" during the initial fetch.
  const isUnknownValue =
    !builtInOptions.find((o) => o.value === value) &&
    !customTypes.find((t) => t.value === value);

  // Flat item list across all groups so <SelectValue /> can resolve the
  // current selection's label (base-ui needs every selectable value in
  // ``items``, groups included).
  const items: { value: string; label: string }[] = [
    ...builtInOptions.map((o) => ({ value: o.value, label: o.label })),
    ...customTypes.map((t) => ({ value: t.value, label: t.label })),
    ...(isUnknownValue && value ? [{ value, label: value }] : []),
    ...(canCreate
      ? [{ value: ADD_NEW_SENTINEL, label: isLoading ? 'Loading…' : '+ Add custom type…' }]
      : []),
  ];

  return (
    <Select value={value} onValueChange={handleSelectChange} items={items}>
      <SelectTrigger className={`w-full ${className ?? ''}`} aria-label="Task type">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {builtInOptions.map((opt) => (
          <SelectItem key={opt.value} value={opt.value}>
            {opt.label}
          </SelectItem>
        ))}
        {customTypes.length > 0 && (
          <SelectGroup>
            <SelectLabel>Your custom types</SelectLabel>
            {customTypes.map((t) => (
              <SelectItem key={t.value} value={t.value}>
                {t.label}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {/* Fall back so an unknown stored value still renders a label
            rather than silently switching to the first option. */}
        {isUnknownValue && value && (
          <SelectItem value={value}>{value}</SelectItem>
        )}
        {canCreate && (
          <SelectItem value={ADD_NEW_SENTINEL}>
            {isLoading ? 'Loading…' : '+ Add custom type…'}
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  );
}
