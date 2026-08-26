/**
 * Several occurrences of ONE callout, stated once.
 *
 * Three trucks changed VIN, so the page showed three strips — and two
 * thirds of their pixels were the same three sentences repeated
 * verbatim, because the explanation belongs to the KIND of statement
 * while only the subject and the evidence belong to the truck.  Four
 * open questions pushed the Vehicles page's own content off the
 * screen, which is how a review queue stops being reviewed.
 *
 * Collapsing them was not the answer: these keys carry
 * `dismiss: 'none'` on purpose — you cannot dismiss a question that is
 * waiting on an answer, and hiding four unanswered questions does not
 * answer them.  The repetition was the waste, not the count.
 *
 * So the split is COMPUTED, never declared: a line whose value is the
 * same for every occurrence is said once in the header; a line that
 * differs becomes a column.  Nothing configures this, which means it
 * cannot go stale when a callout gains a line — and a group of one
 * naturally has no differing lines at all, so it renders as exactly
 * the single strip it was before.
 */
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { resolveCallout, CALLOUT_LINES, type CalloutLineName } from './useCallout';
import type { CalloutData } from './calloutCatalog';
import { toneClasses } from '../../lib/status';
import { Button } from '../ui/button';

/**
 * Rows shown before the queue folds.
 *
 * Deep enough that the ordinary case (a device swap touching a few
 * trucks) never folds at all, shallow enough that a fleet-wide gateway
 * rollout cannot bury the page.  The rest are counted in the button,
 * never silently dropped.
 */
const ROW_CAP = 6;

export default function CalloutGroup<T>({
  items,
  callout,
  actions,
  className = '',
}: {
  /** One entry per occurrence — the feature's own row type. */
  items: T[];
  /** How to read a callout out of one occurrence. */
  callout: (item: T) => CalloutData;
  /**
   * Per-occurrence controls, in the last column.
   *
   * A slot, like `Callout`'s: the vehicle questions answer with "Same
   * truck" / "Different truck…" and the second performs registry
   * surgery.  This component renders whatever it is handed and never
   * asks what the buttons do.
   */
  actions?: (item: T) => ReactNode;
  className?: string;
}) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);

  if (items.length === 0) return null;
  const resolved = items.map((i) => resolveCallout(t, callout(i)));
  const head = resolved[0];

  const valueOf = (r: (typeof resolved)[number], n: CalloutLineName) =>
    r.lines.find((l) => l.name === n)?.value ?? '';
  // Union, not head's lines: an occurrence may answer a line the first
  // one leaves empty, and that difference is exactly what makes it a
  // column rather than a shared sentence.
  const present = CALLOUT_LINES.filter((n) => resolved.some((r) => valueOf(r, n)));
  const shared = present.filter((n) =>
    resolved.every((r) => valueOf(r, n) === valueOf(head, n)));
  const columns = present.filter((n) => !shared.includes(n));

  const rows = showAll ? resolved : resolved.slice(0, ROW_CAP);
  const hidden = resolved.length - rows.length;
  // Each differing line takes only the width it needs; the actions
  // column absorbs the rest, so the values stay in a scannable stack
  // instead of drifting apart across a wide screen.
  const grid = { gridTemplateColumns: `repeat(${columns.length}, max-content) 1fr` };

  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 rounded-lg px-3 py-2.5 ${toneClasses(head.tone)} ${className}`}
    >
      <head.Icon className="size-4 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium">
          {head.title}
          {resolved.length > 1 && (
            // A plain count, not a filled pill: a pill beside a title
            // reads as a status in this app's grammar, and this is a
            // quantity.
            <span className="ml-2 font-normal opacity-70">{resolved.length}</span>
          )}
        </p>
        {shared.length > 0 && (
          <dl className="space-y-0.5 max-w-prose">
            {shared.map((name) => (
              <div key={name} className="flex gap-2 text-xs">
                <dt className="shrink-0 w-16 opacity-70">
                  {t(`callout.labels.${name}`)}
                </dt>
                <dd
                  className={`min-w-0 opacity-90 ${name === 'changed' ? 'font-mono' : ''}`}
                >
                  {valueOf(head, name)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {columns.length > 0 && (
          <div className="grid items-center gap-x-3 gap-y-1 pt-1" style={grid}>
            {/* The labels once, as a column head — repeating them on
                every row is the repetition this component exists to
                remove. */}
            {columns.map((name) => (
              <p
                key={name}
                className="text-2xs font-medium uppercase tracking-wide opacity-60"
              >
                {t(`callout.labels.${name}`)}
              </p>
            ))}
            <span />
            {/* `rows` is a prefix slice, so its index is the item's. */}
            {rows.map((r, i) => (
              <Row
                key={r.key + i}
                values={columns.map((n) => [n, valueOf(r, n)] as const)}
                actions={actions?.(items[i])}
              />
            ))}
          </div>
        )}
        {hidden > 0 && (
          <Button
            type="button" variant="ghost" size="sm"
            onClick={() => setShowAll(true)}
          >
            {t('callout.labels.show_all', { count: String(resolved.length) })}
          </Button>
        )}
      </div>
    </div>
  );
}

/** One occurrence: its differing values, then its own controls. */
function Row({
  values, actions,
}: {
  values: readonly (readonly [CalloutLineName, string])[];
  actions?: ReactNode;
}) {
  return (
    <>
      {values.map(([name, value]) => (
        <span
          key={name}
          className={`text-xs opacity-90 ${name === 'changed' ? 'font-mono' : ''}`}
        >
          {value}
        </span>
      ))}
      <span className="flex flex-wrap items-center gap-2">{actions}</span>
    </>
  );
}
