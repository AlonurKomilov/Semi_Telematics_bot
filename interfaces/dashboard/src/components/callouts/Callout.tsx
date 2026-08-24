/**
 * Callout — the pinned strip.  Page-level or inside a card.
 *
 * The persistent lane's flagship shape.  Not to be confused with
 * `components/banners`, which floats, counts down and disappears: a
 * callout survives a reload because the thing it describes is still
 * true.
 */
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
  const { tone, title, body, fix, Icon } = useCallout(callout);
  return (
    <div
      // role="status", not "alert": this is ambient state that was
      // already true when the page loaded, so it must not interrupt a
      // screen reader mid-sentence the way an arriving alert should.
      role="status"
      className={`flex items-start gap-2.5 rounded-lg px-3 py-2.5 ${toneClasses(tone)} ${className}`}
    >
      <Icon className="size-4 shrink-0 mt-0.5" />
      <div className="min-w-0 space-y-0.5">
        <p className="text-sm font-medium">{title}</p>
        {body && <p className="text-xs opacity-90">{body}</p>}
        {fix && <p className="text-xs opacity-75">{fix}</p>}
      </div>
    </div>
  );
}
