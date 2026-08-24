/**
 * CalloutChip — the compact form for a table cell or a row.
 *
 * Same words as the strip, shrunk to a pill so a list can show which
 * rows carry a caveat without a paragraph per row.  The explanation
 * stays reachable on hover rather than being dropped.
 */
import type { CalloutData } from './calloutCatalog';
import { useCallout } from './useCallout';
import { toneClasses } from '../../lib/status';
import { Tip } from '../tooltip';

export default function CalloutChip({
  callout,
  className = '',
}: {
  callout: CalloutData;
  className?: string;
}) {
  const { title, body, tone } = useCallout(callout);
  return (
    <Tip label={body || title}>
      <span
        className={`inline-flex items-center rounded px-1.5 py-0.5 text-2xs font-medium ${toneClasses(tone)} ${className}`}
      >
        {title}
      </span>
    </Tip>
  );
}
