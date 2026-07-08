/**
 * "When sources disagree" — owner control for vehicle-field source
 * precedence.
 *
 * Samsara and Datatruck both fill in vehicle spec fields (VIN, plate,
 * make, …).  This picks which source WINS each field when they carry
 * different values; the loser only fills the field when it's empty, and
 * an operator's hand-edit always wins regardless.
 *
 * Page-level on the Integrations page because precedence is a policy
 * BETWEEN providers, not a property of one card.  The parent renders it
 * only when 2+ vehicle-writing integrations are connected.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GitMerge } from 'lucide-react';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { getSourcePrecedence, putSourcePrecedence } from './api';
import type { SourcePrecedence } from './api';

const SOURCE_LABEL: Record<string, string> = {
  datatruck: 'Datatruck',
  samsara: 'Samsara',
};

export default function SourcePrecedencePanel() {
  const qc = useQueryClient();

  const { data } = useQuery<SourcePrecedence>({
    queryKey: ['vehicle-source-precedence'],
    queryFn: getSourcePrecedence,
  });

  const mutation = useMutation({
    mutationFn: (primary: Record<string, string>) =>
      putSourcePrecedence(primary),
    onSuccess: (next) =>
      qc.setQueryData(['vehicle-source-precedence'], next),
  });

  // Best-effort panel — stay invisible until the policy loads.
  if (!data) return null;

  // Every field picks from the same source list; build the item set once.
  const sourceItems = data.sources.map((s) => ({ value: s, label: SOURCE_LABEL[s] ?? s }));

  const setPrimary = (field: string, source: string) => {
    const primary: Record<string, string> = {};
    for (const f of data.fields) {
      primary[f.key] = f.key === field ? source : f.primary;
    }
    mutation.mutate(primary);
  };

  return (
    <div className="mt-4 rounded-lg border border-border bg-card p-4">
      <div className="mb-1 flex items-center gap-2">
        <GitMerge size={16} className="text-muted-foreground" />
        <h3 className="text-base font-semibold text-foreground">
          When sources disagree
        </h3>
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        Samsara and Datatruck both fill in vehicle details. Choose which
        source wins each field — the other only fills it when it's empty.
        Your hand-edits always win.
      </p>
      <ul className="divide-y divide-border">
        {data.fields.map((f) => (
          <li
            key={f.key}
            className="flex items-center justify-between gap-3 py-1.5 text-sm"
          >
            <span className="text-foreground">{f.label}</span>
            <Select
              value={f.primary}
              disabled={mutation.isPending}
              onValueChange={(v) => setPrimary(f.key, v)}
              items={sourceItems}
            >
              <SelectTrigger aria-label={`Primary source for ${f.label}`}><SelectValue /></SelectTrigger>
              <SelectContent>
                {sourceItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </li>
        ))}
      </ul>
    </div>
  );
}
