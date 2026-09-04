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

/**
 * What a provider actually DOES for a truck.
 *
 * The marks answer "who supplies this record", and on the vehicle page
 * that is the whole answer — a TMS supplies loads and paperwork, and
 * the page shows those. A MAP is narrower: the only question it asks is
 * who supplies the POSITION, and a TMS badge beside a moving truck
 * claims credit for something it did not do.
 *
 *   telematics — gateways, positions, engine data
 *   tms        — loads, orders, paperwork
 *   local      — a person on 4truck typed it in
 */
export type ProviderRole = 'telematics' | 'tms' | 'local';
export const PROVIDER_ROLE: Record<string, ProviderRole> = {
  samsara: 'telematics',
  datatruck: 'tms',
  manual: 'local',
};

/** The subset a map may speak for.  An unknown provider is EXCLUDED
 *  rather than assumed: claiming a position came from somewhere we
 *  cannot vouch for is the failure this filter exists to prevent. */
export function positionSources(all: string[]): string[] {
  return all.filter((s) => PROVIDER_ROLE[s] === 'telematics');
}

/** Creator first, then everyone who has enriched it — the order is a
 *  fact the server derives from field provenance, not a sort. */
export function orderedSources(
  sources: string[] | null | undefined, source: string | null | undefined,
): string[] {
  const all = sources?.length ? sources : (source ? [source] : []);
  return all.filter((x): x is string => Boolean(x));
}
