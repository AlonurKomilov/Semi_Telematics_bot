/**
 * The one place a callout key becomes rendered words.
 *
 * Every shape (strip, inline note, chip) resolves through this hook so
 * they cannot disagree about what a key says or which tone it wears.
 * Unknown keys degrade to the key itself rather than throwing — a
 * missing translation should look wrong, not take the page down.
 */
import { useTranslation } from 'react-i18next';
import { calloutSpec, type CalloutData } from './calloutCatalog';
import { toneIcon, type Tone } from '../../lib/status';

export interface ResolvedCallout {
  key: string;
  tone: Tone;
  title: string;
  body: string;
  /** What the reader can do about it; empty when nothing applies. */
  fix: string;
  Icon: ReturnType<typeof toneIcon>;
  /** Only `guidance` may ever be dismissed — see calloutCatalog. */
  dismissible: boolean;
}

export function useCallout(c: CalloutData): ResolvedCallout {
  const { t } = useTranslation();
  const spec = calloutSpec(c.key);
  const tone: Tone = spec?.severity ?? 'info';
  // `since` and the backend's params are merged into one substitution
  // bag so copy can say "since {{since}}" or "gateway {{gateway}}"
  // without the component knowing which callout it is rendering.
  const vars = { ...(c.params ?? {}), since: c.since ?? '' };
  const fixKey = `callout.${c.key}.fix`;
  const fix = t(fixKey, vars);
  return {
    key: c.key,
    tone,
    title: t(`callout.${c.key}.title`, vars),
    body: t(`callout.${c.key}.body`, vars),
    // i18next echoes the key back when a string is missing; treat that
    // as "this callout has no fix line" instead of printing the key.
    fix: fix === fixKey ? '' : fix,
    Icon: toneIcon(tone),
    dismissible: spec?.kind === 'guidance',
  };
}
