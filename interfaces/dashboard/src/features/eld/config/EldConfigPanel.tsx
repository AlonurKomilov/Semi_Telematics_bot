/**
 * Hours of Service configuration — the ELD feature's own config surface.
 *
 * It is a PANEL behind the shared `FeatureConfigGear`, in `config/`,
 * named after the surface and not after what is inside it. All three of
 * those are the config family's rules rather than local taste
 * (`capabilities/config/docs/ARCHITECTURE.md`), and this file broke all
 * three: it was `EldSourcePanel` — named after its payload, the thing
 * the rule forbids because it teaches the reader nothing about where the
 * NEXT feature keeps its configuration — and it rendered as a Card
 * halfway down the page, which is the exact anti-pattern the gear
 * component's own docstring lists among the six it replaced.
 *
 * The gating came with the move and is the part that mattered. An
 * account-wide value sat behind no permission check at all: anyone who
 * could read Hours of Service saw the picker. The server refused their
 * PUT, so nothing could be changed — but the UI advertised a door they
 * could not open and let them try, which is what the gear's
 * "render nothing rather than render disabled" rule exists to prevent.
 *
 * WHAT IS INSIDE KEEPS ITS OWN NAME: this holds the ELD reading policy,
 * the way Applications config holds DQF. There is no `/eld/source-config`.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
  from '../../../components/ui/select';

interface EldConfig {
  sources: string[];
  fields: { key: string; label: string; primary: string }[];
  source_labels: Record<string, string>;
  applies_when: string;
}

export default function EldConfigPanel({ deviceCount }: { deviceCount: number }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<EldConfig>({
    queryKey: ['eld-config'],
    queryFn: () => apiJSON<EldConfig>('/eld/config'),
    staleTime: 60_000,
  });
  const save = useMutation({
    mutationFn: (primary: Record<string, string>) =>
      apiJSON<EldConfig>('/eld/config', { method: 'PUT', body: { primary } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eld-config'] });
      // The list is what the choice actually changes.
      qc.invalidateQueries({ queryKey: ['eld-hours'] });
    },
  });

  if (isLoading) {
    return <div className="h-8 rounded-md bg-muted/50 animate-pulse" />;
  }

  const field = data?.fields?.[0];
  const label = (s: string) => data?.source_labels?.[s] ?? s;

  // One device, nothing to arbitrate — and the dialog SAYS so.
  //
  // The old panel rendered null here. That was right while it sat in the
  // page body (a picker for a contest that cannot happen is a decision
  // somebody can get wrong for nothing), and it is wrong behind a gear:
  // a reader who opens a door labelled "configuration" and finds an
  // empty room learns that the page is broken, not that their account
  // has one ELD.
  if (deviceCount <= 1 || !field) {
    return (
      <p className="text-xs text-muted-foreground max-w-prose">
        Nothing to configure yet. This account has one connected
        electronic logging device, so no two devices can disagree about
        the same driver. Connect a second and this is where you choose
        whose reading wins.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">
          When one driver is reported by both devices
        </p>
        {/* Says WHEN it applies, because the honest answer is "almost
            never" — two ELDs normally report different drivers and both
            sets are simply shown together. Without this line the control
            reads as though the account has a conflict it does not. */}
        <p className="text-xs text-muted-foreground max-w-prose">
          {data?.applies_when}
        </p>
      </div>
      <Select
        value={field.primary}
        onValueChange={(v) => save.mutate({ [field.key]: v })}
        disabled={save.isPending}
      >
        <SelectTrigger className="w-full" aria-label={field.label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {(data?.sources ?? []).map((s) => (
            <SelectItem key={s} value={s}>{label(s)}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      {/* The whole reading moves, and saying so is not pedantry: an
          operator who expects per-field merging would read a driver's row
          as a blend of both devices, which is a state neither one ever
          reported. */}
      <p className="text-xs text-muted-foreground max-w-prose">
        The chosen device&apos;s whole reading is shown — duty status and
        its clocks come from one device, never mixed. Hours of service
        cannot be edited here; the ELD is the system of record.
      </p>
    </div>
  );
}
