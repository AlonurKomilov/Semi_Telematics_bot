/**
 * One spelling per provider, and the order provenance is read in.
 *
 * Its own file so the component next door exports only a component
 * (react-refresh), and so the Vehicles column, the detail header and
 * the Source card all read the same map — the label lived in two of
 * those three before, which is how a rename lands in one place.
 */

/** The wire says `manual`; a person reading it gets "Local" — somebody
 *  on 4truck added this truck by hand. */
export const SOURCE_LABEL: Record<string, string> = {
  samsara: 'Samsara',
  datatruck: 'Datatruck',
  manual: 'Local',
};

export const sourceLabel = (x: string): string =>
  SOURCE_LABEL[x] ?? (x ? x.charAt(0).toUpperCase() + x.slice(1) : '');

export interface ProviderLink { source: string; label: string; url: string }

/** Creator first, then everyone who has enriched it — the order is a
 *  fact the server derives from field provenance, not a sort. */
export function orderedSources(
  sources: string[] | null | undefined, source: string | null | undefined,
): string[] {
  const all = sources?.length ? sources : (source ? [source] : []);
  return all.filter((x): x is string => Boolean(x));
}
