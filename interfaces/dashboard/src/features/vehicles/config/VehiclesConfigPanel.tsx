/**
 * Vehicles config — who supplies this roster's values, and what each
 * source may do to it on its own.
 *
 * Field precedence (which source WINS a spec field when two disagree;
 * a hand-edit always wins regardless) plus Auto-pilot (may a source
 * CREATE rows, may its silence-sweep retire them).
 *
 * On the VEHICLES page, where the backend has said it belongs all
 * along: the endpoint is /vehicles/config because the settings are
 * vehicle_field_precedence and source_lifecycle:vehicle — the config
 * family's URL-follows-the-domain-noun rule.  It sat on the
 * Integrations page while precedence was its only content ("a policy
 * BETWEEN providers"); the moment auto-pilot joined, the panel became
 * roster policy, and roster policy belongs to the roster's feature —
 * which is also the shape that scales: driver and load lifecycle
 * policies go to THEIR features' gears, not into one integrations
 * dialog that slowly becomes every entity's junk drawer.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../../components/ui/select';
import { getVehiclesConfig, putVehiclesConfig } from './api';
import type { SourcePrecedence } from './api';
import { useRoleView } from '../../../context/RoleViewContext';
import { Loader2 } from 'lucide-react';
import { Switch } from '../../../components/ui/switch';
import { ErrorState } from '../../../components/shell';

const SOURCE_LABEL: Record<string, string> = {
  datatruck: 'Datatruck',
  samsara: 'Samsara',
};

export default function VehiclesConfigPanel() {
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
    queryFn: getVehiclesConfig,
    enabled: canConfigure,
  });

  const mutation = useMutation({
    mutationFn: (args: {
      primary: Record<string, string>;
      lifecycle?: Record<string, Record<string, boolean>>;
    }) => putVehiclesConfig(args.primary, args.lifecycle),
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

  const currentPrimary = () => {
    const primary: Record<string, string> = {};
    for (const f of data.fields) primary[f.key] = f.primary;
    return primary;
  };

  const setPrimary = (field: string, source: string) => {
    const primary = currentPrimary();
    primary[field] = source;
    mutation.mutate({ primary });
  };

  const setLifecycle = (source: string, verb: string, allowed: boolean) => {
    // The FULL matrix each save, not a delta: the server clamps to the
    // verbs that exist, and sending everything keeps the stored policy
    // equal to what the panel shows.
    const lifecycle: Record<string, Record<string, boolean>> = {};
    for (const srcRow of data.lifecycle?.sources ?? []) {
      lifecycle[srcRow.key] = { ...srcRow.verbs };
    }
    (lifecycle[source] ??= {})[verb] = allowed;
    mutation.mutate({ primary: currentPrimary(), lifecycle });
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

      {(data.lifecycle?.sources?.length ?? 0) > 0 && (
        <>
          {/* Auto-pilot: whether an integration may CHANGE THE ROSTER
              on its own.  Separate switches per verb because "don't
              create trucks I didn't create" and "don't retire trucks I
              didn't retire" are different worries.  A verb a provider
              has no mechanism for is simply absent — a switch that
              stores a lie is worse than none (datatruck cannot
              inactivate anything, so it never shows one).  Turning a
              switch off stops CREATION/RETIREMENT only: matching,
              enrichment and revival keep running, because "stop
              auto-adding vehicles" never means "freeze the trucks I
              already own". */}
          <p className="mt-4 mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Auto-pilot
          </p>
          <p className="mb-2 text-sm text-muted-foreground">
            What each integration may do to the vehicle list on its own.
            Off means new units wait for you to add them, and silent
            trucks are never retired automatically.
          </p>
          <ul className="divide-y divide-border">
            {data.lifecycle!.sources.map((src) => (
              <li
                key={src.key}
                className="flex items-center justify-between gap-3 py-2 text-sm"
              >
                <span className="text-foreground">
                  {SOURCE_LABEL[src.key] ?? src.key}
                </span>
                <span className="flex items-center gap-4">
                  {Object.entries(src.verbs).map(([verb, allowed]) => (
                    <label
                      key={verb}
                      className="flex items-center gap-2 text-xs text-muted-foreground min-h-tap"
                    >
                      {verb === 'add' ? 'May add vehicles' : 'May auto-retire'}
                      <Switch
                        checked={allowed}
                        disabled={mutation.isPending}
                        onCheckedChange={(v) => setLifecycle(src.key, verb, v)}
                        aria-label={`${SOURCE_LABEL[src.key] ?? src.key}: ${
                          verb === 'add' ? 'may add vehicles' : 'may auto-retire'}`}
                      />
                    </label>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
