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
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../../components/ui/select';
import { getIntegrationsConfig, putIntegrationsConfig } from '../api';
import type { SourcePrecedence } from '../api';
import { useRoleView } from '../../../context/RoleViewContext';
import { Loader2 } from 'lucide-react';
import { ErrorState } from '../../../components/shell';

const SOURCE_LABEL: Record<string, string> = {
  datatruck: 'Datatruck',
  samsara: 'Samsara',
};

export default function IntegrationsConfigPanel() {
  const qc = useQueryClient();
  // Precedence is CONFIG, not Manage — it decides which provider WINS per
  // field, so every vehicle read downstream resolves through it.  Both
  // verbs moved to can_manage_config_all, and the Integrations page is
  // gated on can_manage_integrations, so the two now differ: without this
  // the panel would fire a request it cannot be served.
  const { viewHas } = useRoleView();
  const canConfigure = viewHas('can_manage_config_all');

  const { data, isLoading, error } = useQuery<SourcePrecedence>({
    queryKey: ['vehicles-config'],
    queryFn: getIntegrationsConfig,
    enabled: canConfigure,
  });

  const mutation = useMutation({
    mutationFn: (primary: Record<string, string>) =>
      putIntegrationsConfig(primary),
    onSuccess: (next) =>
      qc.setQueryData(['vehicles-config'], next),
  });

  // THREE states, not one.  This was `if (!data) return null`, which is
  // correct for a card in a page's content flow — a panel that quietly
  // omits itself — and wrong the moment it became the whole contents of a
  // dialog the user deliberately opened.  While loading, the dialog was
  // blank; if the fetch FAILED it stayed blank forever, with nothing to
  // distinguish "still working" from "broken".
  if (isLoading) {
    return (
      <div className="flex justify-center py-6">
        <Loader2 className="animate-spin text-muted-foreground size-4.5" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : 'Could not load provider precedence'}
      />
    );
  }

  // Every field picks from the same source list; build the item set once.
  const sourceItems = data.sources.map((s) => ({ value: s, label: SOURCE_LABEL[s] ?? s }));

  const setPrimary = (field: string, source: string) => {
    const primary: Record<string, string> = {};
    for (const f of data.fields) {
      primary[f.key] = f.key === field ? source : f.primary;
    }
    mutation.mutate(primary);
  };

  // No card wrapper and no heading: FeatureConfigGear supplies the
  // dialog, the title and the permission wall. This used to be a card in
  // the page's content flow, which put an account-wide SETTING in the
  // same visual tier as the provider cards next to it — those are
  // operational (connect, sync, test), this decides what every vehicle
  // read resolves to.
  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        Samsara and Datatruck both fill in vehicle details. Choose which
        source wins each field — the other only fills it when it's empty.{' '}
        <span className="text-foreground">Applies to every vehicle</span>;
        existing values re-resolve on the next sync.
      </p>
      <ul className="divide-y divide-border">
        {/* The rule above the choices, styled as one of them: an
            operator's edit is a SOURCE — the highest-ranked one — not a
            footnote.  A row rather than prose so it reads as part of
            the same system the dropdowns configure; fixed text rather
            than a disabled dropdown, because a greyed-out control reads
            as broken while a stated rule reads as designed.  The rank
            itself lives in code (capabilities/source): nothing this
            panel could send can put a provider above a hand edit. */}
        <li className="flex items-start justify-between gap-3 py-2 text-sm">
          <span className="text-foreground">Manual edits</span>
          <span className="text-muted-foreground">Always win</span>
        </li>
        {data.fields.map((f) => (
          <li
            key={f.key}
            className="flex items-start justify-between gap-3 py-2 text-sm"
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
