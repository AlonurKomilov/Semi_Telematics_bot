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
import { ChevronDown, ChevronUp } from 'lucide-react';

import { resolveCallout, CALLOUT_LINES, type CalloutLineName } from './useCallout';
import { useGroupDismissal } from './useDismissal';
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
  const data = items.map(callout);
  // Hooks run unconditionally — the empty-group return is below them.
  const { collapsed, behaviour, close, expand } = useGroupDismissal(
    data[0]?.key ?? '', data.map((c) => c.callout_id ?? ''),
  );

  if (items.length === 0) return null;
  const resolved = data.map((c) => resolveCallout(t, c));
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

  // Folded: the statement stays on screen as one line, because the
  // reason it was worth saying does not stop being true when someone
  // is done reading it.  Same shape as a single `Callout`, with the
  // count kept — the fold must not hide HOW MANY trucks are affected.
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={expand}
        className={`flex items-center gap-2 w-full rounded-lg px-3 py-1.5 text-left min-h-tap ${toneClasses(head.tone)} ${className}`}
      >
        <head.Icon className="size-3.5 shrink-0" />
        <span className="text-xs font-medium min-w-0 truncate">{head.title}</span>
        {resolved.length > 1 && (
          <span className="text-xs opacity-70 shrink-0">{resolved.length}</span>
        )}
        <ChevronDown className="size-3.5 shrink-0 ml-auto opacity-70" />
      </button>
    );
  }

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
        {/* No column varies — one occurrence, or several that differ in
            nothing a reader would use to tell them apart.  The answer
            must NOT depend on that: the actions were rendered inside
            the grid below, so a lone gateway swap showed "Confirm
            below" with nothing below it.  This is the same block
            `Callout` uses, which is what a group of one should be. */}
        {columns.length === 0 && actions && (
          <div className="flex flex-col gap-2 pt-1">
            {rows.map((_, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2">
                {actions(items[i])}
              </div>
            ))}
          </div>
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
      {/* The fold, when the callout declares one.  A group that lost
          this by being grouped would be a callout whose control
          depends on how many trucks happen to have it today. */}
      {behaviour !== 'none' && data.some((c) => c.callout_id) && (
        <button
          type="button"
          onClick={() => { void close(); }}
          aria-label={t('callout.labels.collapse')}
          className="ml-auto shrink-0 rounded p-1 opacity-70 hover:opacity-100 min-h-tap min-w-tap"
        >
          <ChevronUp className="size-3.5" />
        </button>
      )}
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
