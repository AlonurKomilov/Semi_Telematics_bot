/**
 * InfoTip — the ⓘ affordance for learn-once explanations.
 *
 * Field helper text ("saved to the history with your next action",
 * "what proves THIS unit…") is read twice and then becomes visual noise
 * on the first-visible surface.  InfoTip collapses it behind a muted
 * info icon: quiet by default, one hover away — the same rule as the
 * Freshness dot and the attention badges.
 *
 *     <span className={labelCls}>Note <InfoTip label="saved to the history…" /></span>
 *
 * Icon semantics (keep them honest): ⓘ = neutral explanation (this
 * component) · (?) = how-to help · (!) = warnings ONLY — an (!) next to
 * a form label reads as an error.
 *
 * Page descriptions (PageHeader) stay visible — they orient first-time
 * visitors and live in header dead space; this component is for FORM
 * fields and inline labels.
 */
import type { ReactNode } from 'react';
import { Info } from 'lucide-react';
import { Tip } from './Tip';

interface InfoTipProps {
  /** The explanation shown in the bubble. */
  label: ReactNode;
  /** Icon size — 12 for inline-with-small-label, 14 default. */
  size?: 12 | 14 | 16;
}

export function InfoTip({ label, size = 14 }: InfoTipProps) {
  return (
    <Tip label={label}>
      <span
        // Inline-flex keeps the icon on the label's baseline; cursor-help
        // signals "there's an explanation here" without inviting a click.
        className="inline-flex align-middle cursor-help text-muted-foreground/60 hover:text-muted-foreground transition-colors"
        aria-label={typeof label === 'string' ? label : 'More information'}
      >
        <Info size={size} />
      </span>
    </Tip>
  );
}
