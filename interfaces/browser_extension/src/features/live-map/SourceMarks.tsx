/**
 * Who supplies this truck's record, in the panel's selected-vehicle card.
 *
 * The same fact the dashboard shows beside a vehicle's number, drawn
 * with the panel's own chips rather than the dashboard's primitives —
 * an extension ships its own CSS.
 *
 * No links here, unlike the dashboard: a provider deep link needs a
 * per-vehicle URL the panel's token was never given, and a mark that
 * cannot open its door must not look like it can.
 */
import { orderedSources, providerLogo, sourceLabel } from './providerLogos';

const MAX_VISIBLE = 3;

export default function SourceMarks({ sources, source }: {
  sources?: string[] | null;
  source?: string | null;
}) {
  const all = orderedSources(sources, source);
  if (!all.length) return null;
  const shown = all.slice(0, MAX_VISIBLE);
  const rest = all.slice(MAX_VISIBLE);

  return (
    <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
      {shown.map((s, i) => {
        const label = sourceLabel(s);
        const logo = providerLogo(s);
        const why = i === 0 ? `Created by ${label}` : `Enriched by ${label}`;
        return (
          <span key={s} className="mark" title={why}>
            {logo?.kind === 'wordmark'
              ? <logo.Mark className="mark-art" />
              : <>{logo ? <logo.Mark className="mark-art" /> : null}{label}</>}
          </span>
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
