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
 * A provider's own mark, once we are entitled to one.
 *
 * EMPTY ON PURPOSE. The marks render as words until a real asset lands
 * here, and words are not a placeholder — at chip size a logo is a
 * coloured shape most readers have not memorised, while "Samsara" is
 * read. The picture earns its place when there are enough integrations
 * that a name is slower than a glance.
 *
 * To add one:
 *   1. Get the asset FROM the vendor (support / partner contact), not
 *      out of their page markup — a scraped file is the wrong variant
 *      as often as not, and the usage rules come with the real kit.
 *      Ask for the small mark, SVG, on transparent, plus a light
 *      variant if the dark one disappears on a dark UI.
 *   2. Put it with its integration, which is the thing that owns it:
 *      capabilities/integrations/<provider>/assets/logo.svg
 *   3. Serve it once for every client (dashboard, Mini App, the
 *      extension, email and PDF) from a single route rather than
 *      bundling it four times — the extension in particular cannot
 *      import from the Python tree.
 *   4. Add the entry below.
 *
 * ``srcDark`` is optional and only for a mark that vanishes on one of
 * the two themes; a mark that works on both needs just ``src``.
 */
export interface ProviderLogo { src: string; srcDark?: string }
export const PROVIDER_LOGO: Record<string, ProviderLogo> = {};

export const providerLogo = (source: string): ProviderLogo | null =>
  PROVIDER_LOGO[source] ?? null;

/** Creator first, then everyone who has enriched it — the order is a
 *  fact the server derives from field provenance, not a sort. */
export function orderedSources(
  sources: string[] | null | undefined, source: string | null | undefined,
): string[] {
  const all = sources?.length ? sources : (source ? [source] : []);
  return all.filter((x): x is string => Boolean(x));
}
