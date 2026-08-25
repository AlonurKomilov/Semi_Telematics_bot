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
import { useTranslation } from 'react-i18next';
import type { CalloutData } from './calloutCatalog';
import { useCallout } from './useCallout';
import { toneClasses } from '../../lib/status';

export default function Callout({
  callout,
  className = '',
}: {
  callout: CalloutData;
  className?: string;
}) {
  const { t } = useTranslation();
  const { tone, title, why, affects, act, Icon } = useCallout(callout);
  const lines: [string, string][] = [
    [t('callout.labels.why'), why],
    [t('callout.labels.affects'), affects],
    [t('callout.labels.do'), act],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));

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
      </div>
    </div>
  );
}
