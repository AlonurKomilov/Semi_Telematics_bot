/**
 * Which ELD wins when one driver is on two of them.
 *
 * Rendered ONLY when the account actually has more than one connected
 * device reporting hours of service. A picker for a contest that cannot
 * happen is a setting somebody has to read, decide about, and be wrong
 * about — on a page where every other element is answering a question
 * the reader already has.
 *
 * It lives on Hours of Service rather than Integrations because the
 * setting is the ELD feature's own, down to its URL: the chain is
 * source → eld → `/eld/config`. The Vehicles equivalent sits on
 * Integrations for its own historical reasons; copying that placement
 * would put an ELD decision on a page about connecting vendors.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import { Card } from '../../components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
  from '../../components/ui/select';

interface EldConfig {
  sources: string[];
  fields: { key: string; label: string; primary: string }[];
  source_labels: Record<string, string>;
  applies_when: string;
}

export default function EldSourcePanel({ deviceCount }: { deviceCount: number }) {
  const qc = useQueryClient();
  // Hooks before any early return — React needs the same count every
  // render, and this component's visibility flips with the data.
  const { data } = useQuery<EldConfig>({
    queryKey: ['eld-config'],
    queryFn: () => apiJSON<EldConfig>('/eld/config'),
    enabled: deviceCount > 1,
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

  if (deviceCount <= 1 || !data?.fields?.length) return null;

  const field = data.fields[0];
  const label = (s: string) => data.source_labels[s] ?? s;

  return (
    <Card className="p-3 space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="space-y-0.5">
          <p className="text-xs font-medium">
            When one driver is reported by both devices
          </p>
          {/* Says WHEN it applies, because the honest answer is
              "almost never" — two ELDs normally report different
              drivers and both sets are simply shown together. Without
              this line the control reads as though the account has a
              conflict it does not have. */}
          <p className="text-2xs text-muted-foreground max-w-prose">
            {data.applies_when}
          </p>
        </div>
        <Select
          value={field.primary}
          onValueChange={(v) => save.mutate({ [field.key]: v })}
        >
          <SelectTrigger className="w-64" aria-label={field.label}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {data.sources.map((s) => (
              <SelectItem key={s} value={s}>{label(s)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {/* The whole reading moves, and saying so is not pedantry: an
          operator who expects per-field merging would read a driver's
          row as a blend of both devices, which is a state neither one
          ever reported. */}
      <p className="text-2xs text-muted-foreground max-w-prose">
        The chosen device&apos;s whole reading is shown — duty status and
        its clocks come from one device, never mixed. Hours of service
        cannot be edited here; the ELD is the system of record.
      </p>
    </Card>
  );
}
