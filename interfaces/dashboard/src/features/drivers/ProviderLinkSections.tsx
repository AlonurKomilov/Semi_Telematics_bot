/**
 * "Which driver on this device is this person?" — for any integration.
 *
 * The drawer next door carries a hand-written Section for Samsara and
 * another for Datatruck, each with its own picker. That is two, and the
 * third ELD would have been three. This renders a section per provider
 * from data, so a fourth costs nothing.
 *
 * It deliberately does NOT replace those two. They do more than link —
 * Datatruck's backfills loads on save — and folding a bespoke flow into
 * a generic list to make the list look complete would lose the part
 * that is not generic. This picks up every provider they do not cover.
 *
 * WHY THE LIST LOOKS SHORT OF WHAT THE ELD REPORTS
 *
 * The options are the drivers the device is ACTUALLY REPORTING — our
 * own duty feed — not the vendor's roster. ORIENT's driver record
 * carries no role and no active flag, so its roster cannot tell a
 * current driver from somebody who left; a row in the duty feed means a
 * person logged onto a vehicle. The panel says so, because a shorter
 * list than the operator expects reads as a bug otherwise.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import { Badge } from '../../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
  from '../../components/ui/select';
import Section from './Section';

interface ProviderDriver {
  provider_driver_id: string;
  driver_name: string;
  company_code: string;
  vehicle: string;
  linked_user_id: number | null;
}
interface ProviderGroup {
  provider_id: string;
  name: string;
  drivers: ProviderDriver[];
  unlinked: number;
}

/** Samsara and Datatruck keep their own sections in the drawer — they
 *  do more on save than store a link. */
const HAS_ITS_OWN_SECTION = new Set(['samsara', 'datatruck']);

const NONE = '__none__';

export default function ProviderLinkSections({
  userId, onSaved, onError,
}: {
  userId: number;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const qc = useQueryClient();
  // Keyed by provider, not a single id: two providers save independently,
  // and one shared slot means picking in the second one re-enables the
  // first mid-flight — two PUTs to the same endpoint, last response wins
  // instead of last click.
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());

  const { data } = useQuery<{ providers: ProviderGroup[] }>({
    queryKey: ['provider-links'],
    queryFn: () => apiJSON<{ providers: ProviderGroup[] }>('/drivers/provider-links'),
    staleTime: 30_000,
  });

  const save = useMutation({
    mutationFn: ({ providerId, ref }: { providerId: string; ref: string }) =>
      apiJSON(`/drivers/${userId}/provider-links/${encodeURIComponent(providerId)}`,
        { method: 'PUT', body: { provider_driver_id: ref } }),
  });

  const groups = useMemo(
    () => (data?.providers ?? []).filter(
      (g) => !HAS_ITS_OWN_SECTION.has(g.provider_id)),
    [data],
  );

  if (!groups.length) return null;

  const onPick = async (providerId: string, value: string) => {
    const ref = value === NONE ? '' : value;
    setBusy((prev) => new Set(prev).add(providerId));
    try {
      await save.mutateAsync({ providerId, ref });
      onSaved(ref ? 'Driver linked' : 'Driver unlinked');
      qc.invalidateQueries({ queryKey: ['provider-links'] });
      // The duty page reads the link to show a roster name and a truck.
      qc.invalidateQueries({ queryKey: ['eld-hours'] });
      qc.invalidateQueries({ queryKey: ['drivers'] });
    } catch (e) {
      // 409: that driver belongs to another member. Saying which
      // failure it is matters — the operator picked the right person
      // from the right list and still got refused.
      onError(e instanceof Error ? e.message : 'Could not link this driver');
    } finally {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(providerId);
        return next;
      });
    }
  };

  return (
    <>
      {groups.map((g) => {
        const mine = g.drivers.find((d) => d.linked_user_id === userId);
        return (
          <Section key={g.provider_id} title={g.name}>
            <Select
              value={mine?.provider_driver_id ?? NONE}
              onValueChange={(v) => void onPick(g.provider_id, v)}
              disabled={busy.has(g.provider_id)}
            >
              <SelectTrigger className="w-full" aria-label={`${g.name} driver`}>
                <SelectValue placeholder="Not linked" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Not linked</SelectItem>
                {g.drivers.map((d) => {
                  // Taken by somebody else: shown, not hidden. Hiding it
                  // leaves the operator hunting for a name that is right
                  // there on the device, with no way to learn why.
                  const taken = d.linked_user_id !== null
                    && d.linked_user_id !== userId;
                  return (
                    <SelectItem
                      key={d.provider_driver_id}
                      value={d.provider_driver_id}
                      disabled={taken}
                    >
                      {d.driver_name || d.provider_driver_id}
                      {d.company_code ? ` · ${d.company_code}` : ''}
                      {d.vehicle ? ` · ${d.vehicle}` : ''}
                      {taken ? ' — linked to someone else' : ''}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
            <p className="text-2xs text-muted-foreground mt-1">
              Drivers {g.name} is currently reporting
              {g.unlinked > 0 && (
                <> · <Badge tone="warn" subtle>{g.unlinked} unlinked</Badge></>
              )}
            </p>
          </Section>
        );
      })}
    </>
  );
}
