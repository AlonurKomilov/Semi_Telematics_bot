/**
 * Which providers have a mark, and what KIND it is — the panel's copy of
 * the dashboard's registry, because an extension is its own app and
 * cannot import from one.
 *
 * The ARTWORK is not copied by hand: providerMarks.tsx is generated for
 * both apps from the one canonical asset
 * (capabilities/integrations/<provider>/assets/logo.svg). Only this
 * table is per-app, because how a mark is USED is a rendering decision.
 *
 *   wordmark — draws the brand's NAME, so it replaces the text label.
 *   glyph    — a symbol, so it sits beside the name.
 */
import type { ComponentType } from 'react';

import { SamsaraWordmark } from './providerMarks';

export type LogoKind = 'wordmark' | 'glyph';
export interface ProviderLogo {
  Mark: ComponentType<{ className?: string }>;
  kind: LogoKind;
}

/** The wire says `manual`; a person reading it gets "Local". */
export const SOURCE_LABEL: Record<string, string> = {
  samsara: 'Samsara',
  datatruck: 'Datatruck',
  manual: 'Local',
};

export const sourceLabel = (x: string): string =>
  SOURCE_LABEL[x] ?? (x ? x.charAt(0).toUpperCase() + x.slice(1) : '');

/** Creator first, then the enrichers — the order the server derived. */
export function orderedSources(
  sources: string[] | null | undefined, source: string | null | undefined,
): string[] {
  const all = sources?.length ? sources : (source ? [source] : []);
  return all.filter((x): x is string => Boolean(x));
}

export const PROVIDER_LOGO: Record<string, ProviderLogo> = {
  samsara: { Mark: SamsaraWordmark, kind: 'wordmark' },
};

export const providerLogo = (source: string): ProviderLogo | null =>
  PROVIDER_LOGO[source] ?? null;
