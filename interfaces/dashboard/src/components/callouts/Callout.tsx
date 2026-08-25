/**
 * Callout — the pinned strip.  Page-level or inside a card.
 *
 * The persistent lane's flagship shape.  Not to be confused with
 * `components/banners`, which floats, counts down and disappears: a
 * callout survives a reload because the thing it describes is still
 * true.
 *
 * The body is LABELLED ANSWERS, not a paragraph.  A reader arriving
 * at a statement has a handful of standing questions — which record,
 * what changed, what does it mean, what does it cost me, what do I do
 * — and a flat sentence makes them mine it for every one.  Labelled
 * lines let the eye jump straight to the one they care about, and
 * give every future callout the same shape to fill instead of
 * re-inventing prose per fault.
 *
 * Which lines appear is the CALLOUT's choice, not this component's:
 * it renders whatever `useCallout` resolved, in that vocabulary's
 * fixed order.  A caveat qualifying a number has no action and no
 * change; an identity question has both and needs no remedy.  Forcing
 * one fixed set on both is what makes a strip print "Answer below"
 * directly above the answer buttons.
 */
import { useState, type ReactNode } from 'react';
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
  actions,
}: {
  callout: CalloutData;
  className?: string;
  /** What the dismissal is recorded against, e.g. the truck it is on. */
  entity?: { type: string; id: string };
  /**
   * Feature-supplied controls, rendered in one consistent place.
   *
   * A SLOT, deliberately not a field on the wire.  The vehicle
   * identity questions answer with "Same truck" / "Different truck…",
   * and the second performs registry surgery — putting that in the
   * callout contract would make this capability learn what splitting a
   * truck means.  It renders whatever the feature hands it and never
   * asks what the buttons do.
   */
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  const { tone, title, lines, Icon } = useCallout(callout);
  const { dismissed, collapsed, behaviour, close, expand } =
    useDismissal(callout, entity);
  const [failed, setFailed] = useState(false);
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
            {lines.map(({ name, label, value }) => (
              <div key={name} className="flex gap-2 text-xs">
                {/* Fixed label column so the answers line up and the
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
        {actions && (
          <div className="flex flex-wrap items-center gap-2 pt-1">{actions}</div>
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
