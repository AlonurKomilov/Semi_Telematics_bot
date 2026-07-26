/**
 * The service-task picker — ONE dropdown, used by both the maintenance
 * form and the work-order task groups.
 *
 * Replaces the old maintenance-owned TypePicker, which combined a
 * hardcoded built-in array with a per-account "custom types" endpoint.
 * That array was one of three drifting copies of the task vocabulary;
 * options now come entirely from the account's ``service_tasks``.
 *
 * A value the account no longer lists (an archived task on an existing
 * record) is still rendered so editing an old row never silently
 * rewrites its task.
 */
import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X, Loader2 } from 'lucide-react';
import {
  Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
  SelectGroup, SelectLabel,
} from '../../components/ui/select';
import {
  SERVICE_TASKS_KEY, createServiceTask, fetchServiceTasks, taskValue,
  type ServiceTask,
} from './api';

/** Sentinel that opens the inline "add a task" input. */
export const ADD_NEW_SENTINEL = '__add_service_task__';

interface Props {
  value: string;
  onChange: (next: string) => void;
  className?: string;
  /** Gates the inline-create affordance (``can_service_tasks``).
   *  Defaults to true so callers needn't thread the permission. */
  canCreate?: boolean;
  /** Adds a blank "— none —" option (work-order task groups allow an
   *  untagged bucket; maintenance tasks don't). */
  allowEmpty?: boolean;
  emptyLabel?: string;
  /** 'truck' | 'trailer' — hides tasks tagged for the OTHER kind of
   *  unit so a mixed fleet's dropdown stays short. Tasks tagged "any
   *  vehicle" always show, so nothing is ever unreachable. */
  vehicleType?: string;
}

export default function ServiceTaskPicker({
  value, onChange, className, canCreate = true,
  allowEmpty = false, emptyLabel = '— none —', vehicleType = '',
}: Props) {
  const qc = useQueryClient();
  const [inlineAdding, setInlineAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: [...SERVICE_TASKS_KEY, vehicleType || 'any'],
    queryFn: () => fetchServiceTasks(false, vehicleType),
    staleTime: 60_000,
  });

  const create = useMutation({
    mutationFn: (name: string) => createServiceTask({ name }),
    onSuccess: (task) => {
      qc.invalidateQueries({ queryKey: SERVICE_TASKS_KEY });
      onChange(taskValue(task));
      setInlineAdding(false);
      setDraft('');
      setError('');
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : String(e)),
  });

  useEffect(() => {
    if (inlineAdding) inputRef.current?.focus();
  }, [inlineAdding]);

  const tasks: ServiceTask[] = data?.service_tasks ?? [];
  // Subtasks follow their parent, indented (nesting is one level deep,
  // enforced server-side) so a grouped task list reads as a hierarchy
  // instead of a flat alphabetical wall.
  const childrenOf = new Map<number, ServiceTask[]>();
  for (const t of tasks) {
    if (t.parent_id) {
      const list = childrenOf.get(t.parent_id) ?? [];
      list.push(t);
      childrenOf.set(t.parent_id, list);
    }
  }
  const withChildren = (list: ServiceTask[]): Array<[ServiceTask, boolean]> =>
    list.flatMap((t) => [
      [t, false] as [ServiceTask, boolean],
      ...(childrenOf.get(t.id) ?? []).map(
        (c) => [c, true] as [ServiceTask, boolean],
      ),
    ]);
  const topLevel = tasks.filter((t) => !t.parent_id);
  const standard = withChildren(topLevel.filter((t) => t.canonical_key));
  const custom = withChildren(topLevel.filter((t) => !t.canonical_key));
  // An archived/removed task still on this record keeps its slot.
  const known = new Set(tasks.map(taskValue));
  const orphan = value && !known.has(value) ? value : '';

  const onSelect = (next: string) => {
    if (next === ADD_NEW_SENTINEL) {
      if (!canCreate) return;
      setInlineAdding(true);
      return;
    }
    onChange(next);
  };

  const submit = () => {
    const name = draft.trim();
    if (!name) {
      setError('Enter a task name');
      return;
    }
    create.mutate(name);
  };

  if (inlineAdding) {
    return (
      <div className={className}>
        <div className="flex items-center gap-1.5">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => { setDraft(e.target.value); setError(''); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); submit(); }
              if (e.key === 'Escape') { setInlineAdding(false); setError(''); }
            }}
            placeholder="New service task…"
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
          />
          <button
            type="button" onClick={submit} disabled={create.isPending}
            aria-label="Save service task"
            className="p-1.5 rounded-md text-primary hover:bg-muted disabled:opacity-50"
          >
            {create.isPending
              ? <Loader2 size={14} className="animate-spin" />
              : <Check size={14} />}
          </button>
          <button
            type="button"
            onClick={() => { setInlineAdding(false); setError(''); }}
            aria-label="Cancel"
            className="p-1.5 rounded-md text-muted-foreground hover:bg-muted"
          >
            <X size={14} />
          </button>
        </div>
        {error && <p className="mt-1 text-2xs text-destructive">{error}</p>}
      </div>
    );
  }

  return (
    <Select value={value} onValueChange={onSelect}>
      <SelectTrigger className={className ?? 'w-full'} aria-label="Service task">
        <SelectValue placeholder={isLoading ? 'Loading…' : 'Select a task'} />
      </SelectTrigger>
      <SelectContent>
        {allowEmpty && <SelectItem value="">{emptyLabel}</SelectItem>}
        {orphan && <SelectItem value={orphan}>{orphan}</SelectItem>}
        {standard.length > 0 && (
          <SelectGroup>
            <SelectLabel>Standard</SelectLabel>
            {standard.map(([t, isChild]) => (
              <SelectItem key={t.id} value={taskValue(t)}>
                {isChild ? `\u2514 ${t.name}` : t.name}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {custom.length > 0 && (
          <SelectGroup>
            <SelectLabel>Your tasks</SelectLabel>
            {custom.map(([t, isChild]) => (
              <SelectItem key={t.id} value={taskValue(t)}>
                {isChild ? `\u2514 ${t.name}` : t.name}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {canCreate && (
          <SelectItem value={ADD_NEW_SENTINEL}>+ Add service task…</SelectItem>
        )}
      </SelectContent>
    </Select>
  );
}
