/**
 * CalloutInline — the note that stands in for an empty value.
 *
 * Exists because a bare "—" reads as OUR bug.  Rendered where the
 * value would have been ("— not sent by device"), with the full
 * explanation on hover via the tooltip family.
 */
import type { CalloutData } from './calloutCatalog';
import { useCallout } from './useCallout';
import { toneText } from '../../lib/status';
import { Tip } from '../tooltip';

export default function CalloutInline({
  callout,
  /** Shown before the note — usually the em dash it replaces. */
  prefix = '—',
}: {
  callout: CalloutData;
  prefix?: string;
}) {
  const { tone, title, body } = useCallout(callout);
  return (
    <Tip label={body || title}>
      <span className={`inline-flex items-center gap-1 text-xs ${toneText(tone)}`}>
        <span className="text-muted-foreground">{prefix}</span>
        <span>{title}</span>
      </span>
    </Tip>
  );
}
