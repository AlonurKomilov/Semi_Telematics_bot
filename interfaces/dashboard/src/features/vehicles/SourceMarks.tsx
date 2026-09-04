/**
 * Which systems feed this truck — beside its number, wherever the truck
 * is named.
 *
 * The answer already existed twice (the Vehicles list's Source column,
 * the detail page's Source card) and in both places it sat somewhere a
 * person has to go looking: a column that starts hidden, and a card at
 * the foot of the page. Provenance is an identity fact — it belongs
 * next to the identity.
 *
 * Two things are deliberately NOT the same here:
 *
 *   the MARK is who supplies the record — free, already on every
 *   vehicle row, so it can be drawn anywhere the truck appears;
 *
 *   the LINK is a door into that provider's own page for this truck —
 *   resolved separately and only where a real per-vehicle URL exists.
 *   Samsara publishes one; Datatruck does not, and gets a mark without
 *   a link rather than a button that lands on somebody's dashboard
 *   root. A mark must never look like a door it cannot open.
 *
 * A provider with a real mark shows it (providerLogos.ts); one without
 * shows its name. Both are the same chip and the same call site — which
 * is why the marks became a component before there was any artwork to
 * put in one.
 */
import { ExternalLink } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Tip } from '../../components/tooltip';
import { orderedSources, sourceLabel, type ProviderLink } from './sourceLabels';
import { providerLogo } from './providerLogos';

/**
 * What goes inside one chip.
 *
 * A WORDMARK draws the brand's name, so it stands in PLACE of the text —
 * "SAMSARA Samsara" is the thing this prevents. A GLYPH is a symbol,
 * which says nothing to a reader who has not learned it, so it sits
 * beside the name. A provider with no mark is simply its name.
 */
function ChipBody({ source, label }: { source: string; label: string }) {
  const logo = providerLogo(source);
  if (!logo) return <>{label}</>;
  const { Mark, kind } = logo;
  if (kind === 'wordmark') return <Mark className="h-3.5" />;
  return (
    <>
      <Mark className="h-3.5" />
      {label}
    </>
  );
}

/** How many marks stand on their own before the rest fold into a
 *  count.  Three is what a vehicle can carry today (created by one
 *  integration, enriched by another, touched by hand); the cap exists
 *  so a fourth integration widens a tooltip, not the page title. */
export const MAX_VISIBLE_MARKS = 3;

export default function SourceMarks({
  sources, source, links = [], showLabel = false, className = '',
}: {
  sources?: string[] | null;
  source?: string | null;
  links?: ProviderLink[];
  /** Name what the chips ARE. Worth it beside a title, where a bare
   *  "Samsara" could read as a status; noise inside a card that
   *  already says Source in its heading. */
  showLabel?: boolean;
  className?: string;
}) {
  const all = orderedSources(sources, source);
  if (!all.length) return null;
  const linkFor = (s: string) => links.find((l) => l.source === s);
  const shown = all.slice(0, MAX_VISIBLE_MARKS);
  const rest = all.slice(MAX_VISIBLE_MARKS);

  return (
    <span className={`inline-flex flex-wrap items-center gap-1 ${className}`}>
      {showLabel && (
        <span className="text-xs text-muted-foreground">Source</span>
      )}
      {shown.map((s, i) => {
        const label = sourceLabel(s);
        // The creator carries the record; the rest add to it. Saying so
        // means the ORDER does not have to be guessed — and it is not
        // fill-priority, which is per-field and lives in the config gear.
        const why = i === 0 ? `Created by ${label}.` : `Enriched by ${label}.`;
        const link = linkFor(s);
        return (
          <Tip key={s} label={link ? `${why} Opens this truck in ${label}.` : why}>
            {link ? (
              <Badge variant="outline"
                     render={
                       <a href={link.url} target="_blank" rel="noopener noreferrer"
                          aria-label={`${why} ${link.label}`} />
                     }>
                <ChipBody source={s} label={label} />
                <ExternalLink data-icon="inline-end" aria-hidden />
              </Badge>
            ) : (
              <Badge variant="outline" className={i === 0 ? '' : 'text-muted-foreground'}>
                <ChipBody source={s} label={label} />
              </Badge>
            )}
          </Tip>
        );
      })}
      {rest.length > 0 && (
        <Tip label={`Also enriched by ${rest.map(sourceLabel).join(', ')}.`}>
          <Badge variant="outline" className="text-muted-foreground">
            +{rest.length}
          </Badge>
        </Tip>
      )}
    </span>
  );
}
