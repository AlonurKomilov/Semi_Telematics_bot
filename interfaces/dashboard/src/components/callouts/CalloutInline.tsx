/**
 * CalloutInline — the note that stands in for an empty value.
 *
 * Exists because a bare "—" reads as OUR bug.  Renders the callout's
 * SHORT form, not its title: this note lives inside a row that already
 * names the field, so "— No data" is the whole job.  Putting the
 * title here ("— No engine data" in a row labelled Oil Pressure)
 * repeated the category nine times per page and would have needed new
 * wording for every future kind of gap.
 */
import type { CalloutData } from './calloutCatalog';
import { useCallout } from './useCallout';
import { toneText } from '../../lib/status';
import { Tip } from '../tooltip';

export default function CalloutInline({
  callout,
  /** Shown before the note — usually the em dash it replaces. */
  prefix = '—',
  /**
   * The full explanation is ALREADY on this page (the same callout is
   * rendered as a strip), so this note is a pointer, not the
   * explanation — drop the tooltip.
   *
   * Without it, a truck whose engine bus is silent showed the same
   * paragraph nine times on one screen: once in the strip, then again
   * behind every empty field it explains.  Default is off, because
   * where an inline note stands alone — a table cell, a row with no
   * strip above it — the tooltip is the only explanation there is.
   */
  explained = false,
}: {
  callout: CalloutData;
  prefix?: string;
  explained?: boolean;
}) {
  const { tone, short, title, body } = useCallout(callout);
  const note = (
    <span className={`inline-flex items-center gap-1 text-xs ${toneText(tone)}`}>
      <span className="text-muted-foreground">{prefix}</span>
      <span>{short}</span>
    </span>
  );
  return explained ? note : <Tip label={body || title}>{note}</Tip>;
}
