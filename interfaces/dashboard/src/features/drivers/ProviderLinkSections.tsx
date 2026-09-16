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
  /** "telematics" / "TMS" — the sibling sections have always said it. */
  kind: string;
  /** Whether this provider also fills the licence and phone, or only
   *  renames its own hours rows. The copy may not promise the first
   *  for a provider that does not offer it. */
  fills_identity: boolean;
  drivers: ProviderDriver[];
  /** ACCOUNT-wide, not this member's. The caption says so out loud. */
  linked: number;
  total: number;
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

  const { data, isLoading } = useQuery<{ providers: ProviderGroup[] }>({
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

  // Nothing yet vs nothing at all. Returning null while the query is
  // still out makes a slow load look exactly like "no other
  // integration reports drivers here", which is a different and much
  // more final answer.
  if (isLoading) {
    return (
      <Section title="Other integrations">
        <div className="h-8 rounded-md bg-muted/50 animate-pulse" />
      </Section>
    );
  }
  if (!groups.length) return null;

  const onPick = async (providerId: string, value: string) => {
    const ref = value === NONE ? '' : value;
    setBusy((prev) => new Set(prev).add(providerId));
    try {
      await save.mutateAsync({ providerId, ref });
      // Name the consequence, not the event. What unlinking actually
      // stops is visible on another page, so "Driver unlinked" told
      // nobody what they had just given up.
      onSaved(ref
        ? 'Linked — their hours now show this member'
        : 'Unlinked — their hours show the device\'s own name again');
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
          <Section
            key={g.provider_id}
            title={g.kind ? `${g.name} (${g.kind})` : g.name}
          >
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
            {/* What linking DOES, not what the list is. The two
                hand-written sections above both answer that question
                and this one used to describe its own dropdown, which
                gives an admin no reason to act. The count is
                ACCOUNT-wide and says so — inside one person's drawer it
                was being read as that person's own number. */}
            <p className="text-2xs text-muted-foreground mt-1">
              Their hours of service will show this member's name and
              truck
              {g.fills_identity
                ? `, and ${g.name} fills in the licence and phone missing here.`
                : '.'}
              {g.total > 0 && (
                <> · <Badge tone="info" subtle>
                  {g.linked} of {g.total} linked account-wide
                </Badge></>
              )}
            </p>
          </Section>
        );
      })}
    </>
  );
}
