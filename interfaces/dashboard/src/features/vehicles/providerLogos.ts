/**
 * Which providers have a mark, and what KIND it is.
 *
 * ``kind`` is the whole reason this is not a map of URLs:
 *
 *   wordmark — the mark DRAWS the brand's name, so it REPLACES the text
 *              label. Rendering "SAMSARA Samsara" is the bug it prevents.
 *   glyph    — a symbol, which says nothing to a reader who has not
 *              learned it, so it sits BESIDE the name.
 *
 * A provider with no entry is simply its name, which is the right
 * answer until a real asset arrives from the vendor.
 */
import type { ComponentType } from 'react';

import { SamsaraWordmark } from './providerMarks';

export type LogoKind = 'wordmark' | 'glyph';
export interface ProviderLogo {
  Mark: ComponentType<{ className?: string }>;
  kind: LogoKind;
}

export const PROVIDER_LOGO: Record<string, ProviderLogo> = {
  samsara: { Mark: SamsaraWordmark, kind: 'wordmark' },
};

export const providerLogo = (source: string): ProviderLogo | null =>
  PROVIDER_LOGO[source] ?? null;
