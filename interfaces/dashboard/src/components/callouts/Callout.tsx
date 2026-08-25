/**
 * Callout — the pinned strip.  Page-level or inside a card.
 *
 * The persistent lane's flagship shape.  Not to be confused with
 * `components/banners`, which floats, counts down and disappears: a
 * callout survives a reload because the thing it describes is still
 * true.
 *
 * The body is THREE ANSWERS, not a paragraph.  A reader arriving at a
 * degraded truck has the same three questions every time — what is
 * happening, what does it cost me, what do I do — and a flat sentence
 * makes them mine it for all three.  Labelled lines let the eye jump
 * straight to the one they care about, and give every future callout
 * the same shape to fill instead of re-inventing prose per fault.
 *
 * Any line may be absent: a caveat that qualifies a number has no
 * action, so its row is simply not rendered rather than printed with
 * an empty value.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronUp, X } from 'lucide-react';
import type { CalloutData } from './calloutCatalog';
import { useCallout } from './useCallout';
import { useDismissal } from './useDismissal';
import { toneClasses } from '../../lib/status';

export default function Callout({
  callout,
  className = '',
  entity,
}: {
  callout: CalloutData;
  className?: string;
  /** What the dismissal is recorded against, e.g. the truck it is on. */
  entity?: { type: string; id: string };
}) {
  const { t } = useTranslation();
  const { tone, title, why, affects, act, Icon } = useCallout(callout);
  const { dismissed, collapsed, behaviour, close, expand } =
    useDismissal(callout, entity);
  const [failed, setFailed] = useState(false);
  const lines: [string, string][] = [
    [t('callout.labels.why'), why],
    [t('callout.labels.affects'), affects],
    [t('callout.labels.do'), act],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));

  // Removed by this person, and the server has the record.  Nothing
  // renders — but note this is per-USER: a colleague opening the same
  // truck still sees it.
  if (dismissed) return null;

  const onClose = async () => {
    setFailed(false);
    // A dismissal that could not be recorded must not hide anything —
    // the endpoint writes the trail entry first and refuses on failure.
    if (!(await close())) setFailed(true);
  };

  // Collapsed: the statement stays on screen as one line.  This is what
  // keeps a 0-mile truck from reading as a real zero once the strip is
  // out of the way.
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={expand}
        className={`flex items-center gap-2 w-full rounded-lg px-3 py-1.5 text-left min-h-tap ${toneClasses(tone)} ${className}`}
      >
        <Icon className="size-3.5 shrink-0" />
        <span className="text-xs font-medium min-w-0 truncate">{title}</span>
        <ChevronDown className="size-3.5 shrink-0 ml-auto opacity-70" />
      </button>
    );
  }

  return (
    <div
      // role="status", not "alert": this is ambient state that was
      // already true when the page loaded, so it must not interrupt a
      // screen reader mid-sentence the way an arriving alert should.
      role="status"
      className={`flex items-start gap-2.5 rounded-lg px-3 py-2.5 ${toneClasses(tone)} ${className}`}
    >
      <Icon className="size-4 shrink-0 mt-0.5" />
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {lines.length > 0 && (
          <dl className="space-y-0.5">
            {lines.map(([label, value]) => (
              <div key={label} className="flex gap-2 text-xs">
                {/* Fixed label column so three answers line up and the
                    eye can scan down them; it wraps to its own row on
                    narrow screens rather than squeezing the value. */}
                <dt className="shrink-0 w-16 opacity-70">{label}</dt>
                <dd className="min-w-0 opacity-90">{value}</dd>
              </div>
            ))}
          </dl>
        )}
        {failed && (
          <p role="alert" className="text-xs font-medium">
            {t('callout.labels.dismiss_failed')}
          </p>
        )}
      </div>
      {behaviour !== 'none' && callout.callout_id && (
        <button
          type="button"
          onClick={onClose}
          aria-label={t(
            behaviour === 'collapse'
              ? 'callout.labels.collapse'
              : 'callout.labels.dismiss',
          )}
          className="ml-auto shrink-0 rounded p-1 opacity-70 hover:opacity-100 min-h-tap min-w-tap"
        >
          {/* The icon has to mean what the control does.  An X says
              "gone", which is a promise collapse does not keep — and
              the collapsed row already expands with a chevron, so
              folding it back up is that chevron's mirror. */}
          {behaviour === 'collapse'
            ? <ChevronUp className="size-3.5" />
            : <X className="size-3.5" />}
        </button>
      )}
    </div>
  );
}
