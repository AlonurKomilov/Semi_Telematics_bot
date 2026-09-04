/**
 * Who supplies this truck's record, in the panel's selected-vehicle card.
 *
 * The same fact the dashboard shows beside a vehicle's number, drawn
 * with the panel's own chips rather than the dashboard's primitives —
 * an extension ships its own CSS.
 *
 * A mark whose provider publishes a per-vehicle page becomes a link to
 * it; one whose provider does not stays a plain mark. Datatruck has no
 * such URL, and a mark that cannot open its door must not look like it
 * can.
 */
import type { ProviderLink } from './links';
import { orderedSources, positionSources, providerLogo, sourceLabel } from './providerLogos';

const MAX_VISIBLE = 3;

export default function SourceMarks({ sources, source, links = [] }: {
  sources?: string[] | null;
  source?: string | null;
  links?: ProviderLink[];
}) {
  // A panel is a map: it speaks only for who supplies the position.
  const all = positionSources(orderedSources(sources, source));
  if (!all.length) return null;
  const shown = all.slice(0, MAX_VISIBLE);
  const rest = all.slice(MAX_VISIBLE);

  return (
    <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
      {shown.map((s, i) => {
        const label = sourceLabel(s);
        const logo = providerLogo(s);
        const why = i === 0 ? `Created by ${label}` : `Enriched by ${label}`;
        const link = links.find((l) => l.source === s);
        const body = logo?.kind === 'wordmark'
          ? <logo.Mark className="mark-art" />
          : <>{logo ? <logo.Mark className="mark-art" /> : null}{label}</>;
        return link ? (
          <a key={s} className="mark mark-link" href={link.url}
             target="_blank" rel="noopener noreferrer"
             title={`${why} — ${link.label.toLowerCase()}`}
             aria-label={`${why}. ${link.label}`}>
            {body}
            <span aria-hidden style={{ fontSize: 9, opacity: .8 }}>↗</span>
          </a>
        ) : (
          <span key={s} className="mark" title={why}>{body}</span>
        );
      })}
      {rest.length > 0 && (
        <span className="mark" title={`Also enriched by ${rest.map(sourceLabel).join(', ')}`}>
          +{rest.length}
        </span>
      )}
    </span>
  );
}
