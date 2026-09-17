/**
 * Source — where each fact on this driver's record came from.
 *
 * The trucks' Source card says "created by Datatruck, enriched by
 * Samsara" and stops there, because a truck is one record several
 * systems half-know. A PERSON is different in one way that matters:
 * nothing but a human creates one here (an integration may link to a
 * member, fill a blank, never invent a row), so "created by" is always
 * the account and says nothing. What a reader actually asks is
 * per-field — and for the licence number on a DOT record it is a
 * compliance question, not a curiosity: did HR type this, or did the
 * ELD fill it?
 *
 * `manual` renders as "Local", the same word the trucks use, with a
 * "pinned" note: an operator's value outranks every later sync, and a
 * reader deciding whether to trust a number should know that.
 *
 * Renders nothing when the map is empty — a roster nobody has synced
 * or edited has no provenance to state, and an empty card would claim
 * otherwise.
 */
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';
import { sourceLabel } from '../vehicles/sourceLabels';

const FIELDS: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'display_name', label: 'Name' },
  { key: 'phone', label: 'Phone' },
  { key: 'cdl_number', label: 'CDL number' },
  { key: 'cdl_state', label: 'CDL state' },
];

export default function DriverSource({
  provenance,
}: {
  provenance?: Record<string, string> | null;
}) {
  const rows = FIELDS
    .map((f) => ({ ...f, source: provenance?.[f.key] ?? '' }))
    .filter((r) => r.source);
  if (rows.length === 0) return null;

  return (
    <Card className="mt-2">
      <SectionHeader>Source</SectionHeader>
      <dl className="mt-2 space-y-2">
        {rows.map((r) => (
          <div key={r.key} className="flex items-center justify-between gap-3">
            <dt className="text-sm text-muted-foreground">{r.label}</dt>
            <dd className="text-sm text-foreground">
              {sourceLabel(r.source)}
              {r.source === 'manual' && (
                <span className="text-muted-foreground"> · pinned</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
